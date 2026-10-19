"""H7 신뢰성: 타임아웃, 지수 백오프 재시도, 회로 차단기 (대상별 연속 5회 실패 시 60s 차단, Redis cb:{dependency})."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from redis.exceptions import RedisError

from core.errors import AppError, ErrorCode, RetryableNodeError
from core.redis.client import get_redis
from core.settings import load_config

log = logging.getLogger("reliability")
T = TypeVar("T")


def _cfg() -> dict[str, float]:
    c = load_config("budget").get("reliability", {})
    return {"threshold": float(c.get("circuit_fail_threshold", 5)), "open_s": float(c.get("circuit_open_s", 60))}


async def circuit_check(dep: str) -> None:
    try:
        state = await get_redis().hget(f"cb:{dep}", "open")
    except (RedisError, OSError) as e:
        log.warning("circuit state unavailable dep=%s err=%s", dep, type(e).__name__)
        return
    if state == "1":
        raise AppError(
            ErrorCode.UPSTREAM_ERROR, f"circuit open for {dep}", {"dependency": dep}, retry_after_s=_cfg()["open_s"]
        )


async def circuit_record(dep: str, success: bool) -> None:
    r = get_redis()
    key = f"cb:{dep}"
    try:
        if success:
            await r.delete(key)
            return
        fails = await r.hincrby(key, "fails", 1)
        cfg = _cfg()
        await r.expire(key, int(cfg["open_s"]))
        if fails >= cfg["threshold"]:
            await r.hset(key, "open", "1")
            log.warning("circuit opened dep=%s fails=%s", dep, fails)
    except (RedisError, OSError) as e:
        log.warning("circuit record failed dep=%s err=%s", dep, type(e).__name__)


async def with_retries(fn: Callable[[], Awaitable[T]], dep: str, delays: tuple[float, ...] | None = None) -> T:
    """retryable AppError 면 짧게 재시도(기본 0.5s, 2s). 그래도 실패하면 RetryableNodeError (Kafka 재시도로)."""
    if delays is None:
        delays = tuple(load_config("budget").get("mcp", {}).get("inline_retries_s", [0.5, 2.0]))
    last: AppError | None = None
    for i in range(len(delays) + 1):
        await circuit_check(dep)
        try:
            result = await fn()
        except AppError as e:
            if not e.retryable:
                raise
            last = e
            await circuit_record(dep, False)
            if i < len(delays):
                wait = max(delays[i], min(e.retry_after_s or 0, 5.0))
                log.info("retryable %s from %s, retry in %.1fs", e.code, dep, wait)
                await asyncio.sleep(wait)
            continue
        await circuit_record(dep, True)
        return result
    assert last is not None
    raise RetryableNodeError(last.code, last.message, last.details, last.retry_after_s)
