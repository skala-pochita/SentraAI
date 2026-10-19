"""대상 감사 락 lock:audit:target:{target_id} (SET NX EX 1800)."""

from __future__ import annotations

from core.errors import AppError, ErrorCode
from core.redis.client import get_redis

LOCK_TTL_S = 1800

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
