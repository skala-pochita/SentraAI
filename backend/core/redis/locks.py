"""대상 감사 락 lock:audit:target:{target_id} (SET NX EX 1800).

획득·해제·연장은 모두 Lua 스크립트 한 번으로 처리한다 (소유자 확인과 변경 사이에 다른 감사가 끼어들 수 없음).
GET → DEL 로 나누면, 그 사이 내 락이 만료되고 다른 감사가 새로 잡은 락을 지울 수 있다 (TOCTOU, 2026-11-06 리뷰 A).
"""

from __future__ import annotations

import asyncio
import logging

from redis.exceptions import RedisError

from core.errors import AppError, ErrorCode
from core.redis.client import get_redis

LOCK_TTL_S = 1800
log = logging.getLogger("locks")

# 반환: 1 = 획득(또는 같은 감사의 재획득·TTL 갱신), 그 외 = 현재 소유자 audit_id
_ACQUIRE = """
if redis.call('set', KEYS[1], ARGV[1], 'NX', 'EX', ARGV[2]) then return 1 end
local holder = redis.call('get', KEYS[1])
if holder == ARGV[1] then redis.call('expire', KEYS[1], ARGV[2]) return 1 end
return holder
"""

_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end
"""

# 여러 대상 락 중 이 감사가 쥔 것만 TTL 을 늘린다. 반환: 연장한 개수
_EXTEND = """
local n = 0
for i, key in ipairs(KEYS) do
  if redis.call('get', key) == ARGV[1] then n = n + redis.call('expire', key, ARGV[2]) end
end
return n
"""


def _key(target_id: str) -> str:
    return f"lock:audit:target:{target_id}"


async def acquire_target_lock(target_id: str, audit_id: str) -> None:
    """같은 audit 의 재획득(재전달·재개)은 허용, 다른 audit 이 점유 중이면 CONFLICT."""
    res = await get_redis().eval(_ACQUIRE, 1, _key(target_id), audit_id, LOCK_TTL_S)
    if res == 1:
        return
    raise AppError(
        ErrorCode.CONFLICT, "target is being audited by another audit", {"target_id": target_id, "holder": res}
    )


async def release_target_lock(target_id: str, audit_id: str) -> bool:
    """이 감사가 쥔 락일 때만 지운다. 지웠으면 True."""
    return bool(await get_redis().eval(_RELEASE, 1, _key(target_id), audit_id))


async def extend_target_locks(target_ids: list[str], audit_id: str, ttl_s: int = LOCK_TTL_S) -> int:
    """이 감사가 쥔 락만 TTL 을 다시 늘린다 (다른 감사의 락은 건드리지 않음). 연장한 개수를 돌려준다."""
    if not target_ids:
        return 0
    return int(await get_redis().eval(_EXTEND, len(target_ids), *[_key(t) for t in target_ids], audit_id, ttl_s))


async def keep_locks_alive(
    target_ids: list[str], audit_id: str, interval_s: float = LOCK_TTL_S / 3, ttl_s: int = LOCK_TTL_S
) -> None:
    """감사가 끝날 때까지 락 TTL 을 주기적으로 연장한다 (긴 감사 중 락 만료 → 같은 대상 동시 감사 방지).
    호출자가 cancel 한다. Redis 오류는 경고만 남기고 다음 주기에 다시 시도한다.
    연장한 개수가 대상 수보다 적으면 락을 잃은 것이다 (만료 후 다른 감사가 잡음) → 경고를 남긴다."""
    while True:
        await asyncio.sleep(interval_s)
        try:
            n = await extend_target_locks(target_ids, audit_id, ttl_s)
        except (RedisError, OSError) as e:
            log.warning("lock heartbeat failed audit=%s err=%s", audit_id, type(e).__name__)
            continue
        if n < len(target_ids):
            log.warning("lock lost audit=%s held=%d/%d", audit_id, n, len(target_ids))
