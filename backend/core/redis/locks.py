"""대상 감사 락 lock:audit:target:{target_id} (SET NX EX 1800)."""

from __future__ import annotations

import asyncio
import logging

from redis.exceptions import RedisError

from core.errors import AppError, ErrorCode
from core.redis.client import get_redis

LOCK_TTL_S = 1800
log = logging.getLogger("locks")

_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end
"""


async def acquire_target_lock(target_id: str, audit_id: str) -> None:
    """같은 audit 의 재획득(재전달·재개)은 허용, 다른 audit 이 점유 중이면 CONFLICT."""
    r = get_redis()
    key = f"lock:audit:target:{target_id}"
    if await r.set(key, audit_id, nx=True, ex=LOCK_TTL_S):
        return
    holder = await r.get(key)
    if holder == audit_id:
        await r.expire(key, LOCK_TTL_S)
        return
    raise AppError(
        ErrorCode.CONFLICT, "target is being audited by another audit", {"target_id": target_id, "holder": holder}
    )


async def release_target_lock(target_id: str, audit_id: str) -> None:
    r = get_redis()
    key = f"lock:audit:target:{target_id}"
    if await r.get(key) == audit_id:
        await r.delete(key)


async def extend_target_locks(target_ids: list[str], audit_id: str, ttl_s: int = LOCK_TTL_S) -> int:
    """이 감사가 쥔 락만 TTL 을 다시 늘린다 (다른 감사의 락은 건드리지 않음). 연장한 개수를 돌려준다."""
    r = get_redis()
    n = 0
    for tid in target_ids:
        key = f"lock:audit:target:{tid}"
        if await r.get(key) == audit_id and await r.expire(key, ttl_s):
            n += 1
    return n


async def keep_locks_alive(
    target_ids: list[str], audit_id: str, interval_s: float = LOCK_TTL_S / 3, ttl_s: int = LOCK_TTL_S
) -> None:
    """감사가 끝날 때까지 락 TTL 을 주기적으로 연장한다 (긴 감사 중 락 만료 → 같은 대상 동시 감사 방지).
    호출자가 cancel 한다. Redis 오류는 경고만 남기고 다음 주기에 다시 시도한다."""
    while True:
        await asyncio.sleep(interval_s)
        try:
            await extend_target_locks(target_ids, audit_id, ttl_s)
        except (RedisError, OSError) as e:
            log.warning("lock heartbeat failed audit=%s err=%s", audit_id, type(e).__name__)
