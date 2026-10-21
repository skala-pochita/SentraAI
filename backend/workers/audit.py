"""audit-worker: audit.requested 소비 → LangGraph 실행 (명세 8.6, 11).

- 체크포인터: AsyncRedisSaver (thread_id = audit:{audit_id}, TTL 7일)
- 재전달(워커 장애·Kafka 재시도) 시 같은 thread_id 로 실행 → 마지막 checkpoint 부터 재개 (처음부터 다시 하지 않음)
- 읽기 전용 그래프에는 interrupt 가 없다. 과거 audit.resume 메시지는 무시한다 (DECISIONS 2026-10-21)
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph

from core.db.models import Audit, AuditTarget
from core.db.session import session_scope
from core.errors import AppError, ErrorCode, RetryableNodeError
from core.graph.build import build_graph
from core.graph.deps import Deps
from core.kafka.consumer import Message
from core.kafka.envelope import Envelope
from core.kafka.producer import publish
from core.mcp_client import Gateway, http_gateway
from core.redis.locks import release_target_lock
from harness.events import emit
from harness.observability import langsmith_config, set_trace_context, trace_id_for

log = logging.getLogger("worker.audit")

CHECKPOINT_TTL_MIN = 7 * 24 * 60
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


async def make_checkpointer() -> Any:
    """AsyncRedisSaver (Redis Stack 필요). 컨텍스트 매니저를 반환한다."""
    from langgraph.checkpoint.redis.aio import AsyncRedisSaver

    from core.settings import get_settings

    return AsyncRedisSaver.from_conn_string(get_settings().redis_url, ttl={"default_ttl": CHECKPOINT_TTL_MIN})


async def _set_audit(audit_id: str, **fields: Any) -> Audit | None:
    async with session_scope() as db:
        a = await db.get(Audit, uuid.UUID(audit_id))
        if a is None:
            return None
        for k, v in fields.items():
            setattr(a, k, v)
        return a


class AuditRunner:
    def __init__(
        self, checkpointer: BaseCheckpointSaver, gateway_factory: Any = http_gateway, deps_factory: Any = None
    ) -> None:
        self.graph: CompiledStateGraph = build_graph(checkpointer)  # type: ignore[type-arg]
        self.gateway_factory = gateway_factory
        self.deps_factory = deps_factory

    def _config(self, audit_id: str) -> RunnableConfig:
        gw: Gateway = self.gateway_factory()
        deps = self.deps_factory(gw) if self.deps_factory else Deps(gateway=gw)
        return cast(
            RunnableConfig,
            {
                "configurable": {"thread_id": f"audit:{audit_id}", "deps": deps},
                "recursion_limit": 200,
                **langsmith_config(audit_id),
            },
        )

    async def _initial(self, audit: Audit) -> dict[str, Any]:
        async with session_scope() as db:
            from sqlalchemy import select

            rows = (await db.execute(select(AuditTarget.target_id).where(AuditTarget.audit_id == audit.id))).scalars()
            target_ids = [str(t) for t in rows]
        from core.db.models import User

        async with session_scope() as db:
            user = await db.get(User, audit.requested_by)
            role = user.role if user else "ADMIN"
        return {
            "audit_id": str(audit.id),
            "thread_id": audit.thread_id,
            "requested_by": str(audit.requested_by),
            "actor_role": role,
            "mode": audit.mode,
            "eval_config": audit.eval_config,
            "targets": [{"target_id": t} for t in target_ids],
            "errors": [],
            "token_usage": [],
        }

    async def handle(self, msg: Message) -> None:
        if msg.topic == "audit.requested":
            await self.run(msg.envelope.payload["audit_id"])
        else:
            log.warning("ignored topic %s (read-only audit has no resume)", msg.topic)

    async def run(self, audit_id: str) -> str:
        async with session_scope() as db:
            audit = await db.get(Audit, uuid.UUID(audit_id))
        if audit is None:
            raise AppError(ErrorCode.NOT_FOUND, f"audit {audit_id} not found")
        if audit.status in TERMINAL:
            log.info("audit %s already %s; skip", audit_id, audit.status)
            return audit.status
        set_trace_context(audit_id)
        config = self._config(audit_id)
        snap = await self.graph.aget_state(config)
        inp: dict[str, Any] | None = None  # 체크포인트가 있으면 이어서
        if not snap.values:
            inp = await self._initial(audit)
        elif snap.next:
            log.info("audit %s resuming from checkpoint (next=%s)", audit_id, snap.next)
        await _set_audit(audit_id, status="RUNNING", started_at=audit.started_at or datetime.now(UTC))
        await emit(audit_id, "audit.running", {"resumed": inp is None})
        try:
            if inp is not None or snap.next:
                await self.graph.ainvoke(inp, config)
        except RetryableNodeError:
            await emit(audit_id, "audit.retrying", {})
            raise
        except AppError as e:
            await self._fail(audit_id, e)
            raise
        return await self._complete(audit_id, (await self.graph.aget_state(config)).values)

    async def _complete(self, audit_id: str, v: dict[str, Any]) -> str:
        counts: dict[str, int] = {}
        for f in v.get("correlated", []):
            counts[f["severity"]] = counts.get(f["severity"], 0) + 1
        summary = {
            "by_severity": counts,
            "total": len(v.get("correlated", [])),
            "report_id": v.get("report_id"),
            "risk_paths": len(v.get("risk_paths", [])),
            "tokens": sum(u.get("in", 0) + u.get("out", 0) for u in v.get("token_usage", [])),
            "usd": round(sum(u.get("usd", 0) for u in v.get("token_usage", [])), 6),
        }
        await _set_audit(
            audit_id, status="COMPLETED", finished_at=datetime.now(UTC), current_node="report", summary=summary
        )
        await emit(audit_id, "audit.completed", summary, progress=1.0)
        await publish(
            "audit.completed",
            audit_id,
            Envelope(
                type="audit.completed", trace_id=trace_id_for(audit_id), payload={"audit_id": audit_id, **summary}
            ),
        )
        return "COMPLETED"

    async def _fail(self, audit_id: str, e: AppError) -> None:
        from core.sandbox_client import sandbox_call
        try:
            await sandbox_call('/stop/' + audit_id, timeout=30)
        except AppError:
            log.warning("sandbox cleanup unavailable for failed audit %s; TTL reaper will retry", audit_id)
        await _set_audit(audit_id, status="FAILED", finished_at=datetime.now(UTC))
        await emit(audit_id, "audit.failed", {"code": str(e.code), "message": e.message})
        async with session_scope() as db:
            from sqlalchemy import select

            tids = (
                await db.execute(select(AuditTarget.target_id).where(AuditTarget.audit_id == uuid.UUID(audit_id)))
            ).scalars()
            for t in list(tids):
                await release_target_lock(str(t), audit_id)
