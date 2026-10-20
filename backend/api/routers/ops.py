"""/ops/* (명세 14장): 의존성 헬스, DLQ 목록·재처리."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import Admin, CurrentUser
from core.db.models import DlqMessage
from core.db.session import get_db, get_engine
from core.errors import AppError, ErrorCode
from core.kafka.producer import publish
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
    """deep=false: 프로세스 생존(컨테이너 healthcheck). deep=true: 의존성 전체 (DB, Redis, Kafka, MCP 2개, target-agent)."""
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


@router.get("/dlq")
async def list_dlq(
    status: str | None = None, _: CurrentUser = Admin, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    q = select(DlqMessage).order_by(DlqMessage.created_at.desc())
    if status:
        q = q.where(DlqMessage.status == status)
    rows = [
        {
            "id": str(m.id),
            "original_topic": m.original_topic,
            "message_key": m.message_key,
            "message": m.message,
            "headers": m.headers,
            "error_code": m.error_code,
            "error_message": m.error_message,
            "status": m.status,
            "created_at": m.created_at.isoformat(),
        }
        for m in (await db.execute(q.limit(200))).scalars()
    ]
    r = get_redis()
    retrying = 0
    async for _k in r.scan_iter(match="kafka:processed:*", count=500):
        value = await r.get(_k)
        if isinstance(value, bytes):
            value = value.decode()
        retrying += 1 if (value or "").startswith("processing") else 0
    new = (
        await db.execute(select(func.count()).select_from(DlqMessage).where(DlqMessage.status == "NEW"))
    ).scalar_one()
    return {"items": rows, "new_count": new, "processing": retrying}


@router.post("/dlq/{dlq_id}/replay")
async def replay(dlq_id: uuid.UUID, _: CurrentUser = Admin, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    m = await db.get(DlqMessage, dlq_id)
    if m is None:
        raise AppError(ErrorCode.NOT_FOUND, "dlq message not found")
    if m.status == "REPLAYED":
        raise AppError(ErrorCode.CONFLICT, "already replayed")
    import json

    # retry_count 초기화 + 새 message_id 로 재발행 (멱등 키 충돌 방지)
    msg = dict(m.message)
    msg["message_id"] = str(uuid.uuid4())
    await publish(
        m.original_topic,
        m.message_key or "",
        json.dumps(msg).encode(),
        {"retry_count": "0", "original_topic": m.original_topic, "replayed_from": str(m.id)},
    )
    m.status = "REPLAYED"
    return {"id": str(m.id), "status": m.status, "topic": m.original_topic}
