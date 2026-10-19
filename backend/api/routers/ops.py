"""/ops/* (명세 14장). 의존성 헬스."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import httpx
from fastapi import APIRouter
from sqlalchemy import text

from core.db.session import get_engine
from core.redis.client import get_redis
from core.settings import get_settings

router = APIRouter(prefix="/ops", tags=["ops"])
log = logging.getLogger("api.ops")


async def _db() -> None:
    async with get_engine().connect() as conn:
        await conn.execute(text("SELECT 1"))


async def _redis() -> None:
    await get_redis().ping()


async def _kafka() -> None:
    host, _, port = get_settings().kafka_bootstrap.split(",")[0].partition(":")
    _, writer = await asyncio.open_connection(host, int(port or 9092))
    writer.close()
    await writer.wait_closed()


def _http(url: str) -> Callable[[], Awaitable[None]]:
    async def check() -> None:
        async with httpx.AsyncClient(timeout=2) as c:
            (await c.get(url)).raise_for_status()

    return check


async def _check(name: str, fn: Callable[[], Awaitable[None]]) -> tuple[str, dict[str, str]]:
    try:
        await asyncio.wait_for(fn(), timeout=3)
        return name, {"status": "ok"}
    except Exception as e:  # noqa: BLE001 - 헬스체크는 모든 실패를 상태로 보고
        return name, {"status": "down", "error": type(e).__name__}


@router.get("/health")
async def health(deep: bool = False) -> dict[str, object]:
    """deep=false: 프로세스 생존(컨테이너 healthcheck). deep=true: 의존성 전체."""
    if not deep:
        return {"status": "ok", "dependencies": {}}
    s = get_settings()
    checks = {
        "db": _db,
        "redis": _redis,
        "kafka": _kafka,
        "business-mcp": _http(s.business_mcp_url.removesuffix("/mcp") + "/health"),
        "rag-mcp": _http(s.rag_mcp_url.removesuffix("/mcp") + "/health"),
        "target-agent": _http(s.target_agent_url + "/health"),
    }
    results = dict(await asyncio.gather(*(_check(n, f) for n, f in checks.items())))
    status = "ok" if all(v["status"] == "ok" for v in results.values()) else "degraded"
    return {"status": status, "dependencies": results}
