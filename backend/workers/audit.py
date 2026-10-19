"""audit-worker: audit.requested / audit.resume / github.webhook 소비 → LangGraph 실행 (명세 8.6, 11).

- 체크포인터: AsyncRedisSaver (thread_id = audit:{audit_id}, TTL 7일)
- 재전달(워커 장애·Kafka 재시도) 시 같은 thread_id 로 실행 → 마지막 checkpoint 부터 재개 (처음부터 다시 하지 않음)
- interrupt 지점(human_approval, wait_merge)에서 멈추면 감사 상태 WAITING_APPROVAL / WAITING_MERGE
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command

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
from harness.observability import set_trace_context

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

    def _config(self, audit_id: str) -> dict[str, Any]:
        gw: Gateway = self.gateway_factory()
        deps = self.deps_factory(gw) if self.deps_factory else Deps(gateway=gw)
        return {"configurable": {"thread_id": f"audit:{audit_id}", "deps": deps}, "recursion_limit": 200}

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
        p = msg.envelope.payload
        if msg.topic == "audit.requested":
            await self.run(p["audit_id"])
        elif msg.topic == "audit.resume":
            await self.run(p["audit_id"], resume=p.get("resume_value"), resume_kind=p.get("kind"))
        else:
            log.warning("unexpected topic %s", msg.topic)

    async def run(self, audit_id: str, resume: Any = None, resume_kind: str | None = None) -> str:
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
        if resume is not None:
            if not snap.next:
                log.warning("resume for audit %s but graph is not interrupted; ignored", audit_id)
                return audit.status
            pending = self._interrupt_kind(snap)
            if resume_kind and pending and resume_kind != pending:
                log.warning("resume kind %s != pending %s for %s; ignored", resume_kind, pending, audit_id)
                return audit.status
            inp: Any = Command(resume=resume)
        elif snap.values:
            if not snap.next:
                inp = None
            elif self._interrupt_kind(snap):  # 재전달된 요청인데 이미 interrupt 대기 중
                return await self._after(audit_id, snap)
            else:
                inp = None  # 체크포인트부터 이어서
            log.info("audit %s resuming from checkpoint (next=%s)", audit_id, snap.next)
        else:
            inp = await self._initial(audit)
        await _set_audit(audit_id, status="RUNNING", started_at=audit.started_at or datetime.now(UTC))
        await emit(audit_id, "audit.running", {"resumed": inp is None or resume is not None})
        try:
            await self.graph.ainvoke(inp, config)
        except RetryableNodeError:
            await emit(audit_id, "audit.retrying", {})
            raise
        except AppError as e:
            await self._fail(audit_id, e)
            raise
        return await self._after(audit_id, await self.graph.aget_state(config))

    @staticmethod
    def _interrupt_kind(snap: Any) -> str | None:
        for t in snap.tasks or []:
            for it in getattr(t, "interrupts", []) or []:
                if isinstance(it.value, dict):
                    return str(it.value.get("type"))
        return None

    async def _after(self, audit_id: str, snap: Any) -> str:
        if snap.next:
            kind = self._interrupt_kind(snap)
            status = "WAITING_APPROVAL" if kind == "approval" else "WAITING_MERGE"
            await _set_audit(audit_id, status=status, current_node=snap.next[0])
            await emit(audit_id, f"audit.{status.lower()}", {"node": snap.next[0]}, node=snap.next[0])
            return status
        v = snap.values
        counts: dict[str, int] = {}
        for f in v.get("correlated", []):
            counts[f["severity"]] = counts.get(f["severity"], 0) + 1
        summary = {
            "by_severity": counts,
            "total": len(v.get("correlated", [])),
            "report_id": v.get("report_id"),
            "risk_paths": len(v.get("risk_paths", [])),
            "verify_result": v.get("verify_result"),
            "tokens": sum(u.get("in", 0) + u.get("out", 0) for u in v.get("token_usage", [])),
            "usd": round(sum(u.get("usd", 0) for u in v.get("token_usage", [])), 6),
        }
        await _set_audit(
            audit_id, status="COMPLETED", finished_at=datetime.now(UTC), current_node="report", summary=summary
        )
        await emit(audit_id, "audit.completed", summary, progress=1.0)
        await publish(
            "audit.completed", audit_id, Envelope(type="audit.completed", payload={"audit_id": audit_id, **summary})
        )
        return "COMPLETED"

    async def _fail(self, audit_id: str, e: AppError) -> None:
        await _set_audit(audit_id, status="FAILED", finished_at=datetime.now(UTC))
        await emit(audit_id, "audit.failed", {"code": str(e.code), "message": e.message})
        async with session_scope() as db:
            from sqlalchemy import select

            tids = (
                await db.execute(select(AuditTarget.target_id).where(AuditTarget.audit_id == uuid.UUID(audit_id)))
            ).scalars()
            for t in list(tids):
                await release_target_lock(str(t), audit_id)


async def webhook_to_resume(msg: Message) -> None:
    """github.webhook (pull_request.closed + merged) → audit.resume. 브랜치 이름 fix/<audit8>-<proposal8>."""
    p = msg.envelope.payload
    pr = p.get("pull_request", {})
    if p.get("action") != "closed" or not pr.get("merged"):
        return
    audit_id = p.get("audit_id")
    if not audit_id:
        log.info("webhook without audit mapping; ignored")
        return
    await publish(
        "audit.resume",
        audit_id,
        Envelope(
            type="audit.resume",
            payload={"audit_id": audit_id, "kind": "merge", "resume_value": {"merged": True, "source": "github"}},
        ),
    )
