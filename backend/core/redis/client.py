"""Redis 클라이언트 (싱글턴, 테스트에서 교체 가능)."""

from __future__ import annotations

from redis.asyncio import Redis

from core.settings import get_settings

_client: Redis | None = None


def get_redis() -> Redis:
    global _client
    if _client is None:
        _client = Redis.from_url(
            get_settings().redis_url, decode_responses=True, socket_timeout=5, socket_connect_timeout=5
        )
    return _client


def set_redis(client: Redis | None) -> None:
    """테스트용: fakeredis 등으로 교체."""
    global _client
    _client = client
