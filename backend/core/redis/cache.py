"""JSON 캐시 (cache:scan:*, cache:rag:*). Redis 장애 시 캐시 없이 동작하고 경고만 남긴다."""

from __future__ import annotations

import json
import logging
from typing import Any

from redis.exceptions import RedisError

from core.redis.client import get_redis

log = logging.getLogger("cache")


async def cache_get(key: str) -> Any | None:
    try:
        raw = await get_redis().get(key)
    except (RedisError, OSError) as e:
        log.warning("cache get failed key=%s err=%s", key, type(e).__name__)
        return None
    return json.loads(raw) if raw else None


async def cache_set(key: str, value: Any, ttl_s: int) -> None:
    try:
        await get_redis().set(key, json.dumps(value, ensure_ascii=False, default=str), ex=ttl_s)
    except (RedisError, OSError) as e:
        log.warning("cache set failed key=%s err=%s", key, type(e).__name__)
