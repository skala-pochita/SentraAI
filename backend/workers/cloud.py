"""Agent·Cloud 감사 실행기 (기획서 6장): Kafka audit.requested/audit.resume(payload 에 task_id) → LangGraph 실행.

- 업무 상태의 기준은 DB(cloud_tasks). 워커는 MCP(business/rag) 로만 업무 데이터를 읽고 티켓을 만든다.
- 체크포인터 thread_id = cloud:{task_id}. 승인 대기 중 워커가 재시작돼도 같은 thread 로 이어 실행한다.
- 이벤트는 노드 사이마다 (task_id, eventSeq) 유일 제약으로 저장한다 (재전달은 같은 번호 → 무시).
- 보고서는 answer 로 저장한 뒤에만 DONE 이벤트를 저장한다.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from core.cloud.backend_mcp import McpBackend
from core.cloud.budget import CloudBudget
from core.cloud.bundle import parse_bundle
from core.cloud.eval_runner import make_runner
from core.cloud.pipeline import CloudCtx, resume_cloud_audit, run_cloud_audit
from core.db.models import CloudApproval, CloudTask, CloudTaskEvent
from core.db.session import session_scope
from core.errors import AppError, ErrorCode
from core.kafka.consumer import Message
from core.mcp_client import Gateway, http_gateway

log = logging.getLogger("worker.cloud")
TERMINAL = {"COMPLETED", "FAILED"}
GUIDANCE_URI = "guidance://security-baseline/v1"
DEADLINE_MARGIN_S = 15  # 전송·정리 여유 (추가 모델 실행 시간이 아니다)


def deadline_for(remaining_s: float) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=remaining_s + DEADLINE_MARGIN_S)


class CloudRunner:
    def __init__(
        self, checkpointer: BaseCheckpointSaver, gateway_factory: Any = http_gateway, runner_factory: Any = make_runner
    ) -> None:
        self.checkpointer = checkpointer
        self.gateway_factory = gateway_factory
        self.runner_factory = runner_factory

    async def handle(self, msg: Message) -> None:
        p = msg.envelope.payload
        if p.get("kind") == "cloud_approval":
            await self.resume(p["task_id"], p["approval_id"])
        else:
            await self.start(p["task_id"])

    # ---- ctx ----
    def _ctx(self, task: CloudTask, budget: CloudBudget | None = None) -> CloudCtx:
        from core.cloud.tools import ToolHost

        bundle = parse_bundle(task.bundle)
        budget = budget or CloudBudget.from_config()
        runner, model = self.runner_factory("B", budget)
        task_id = str(task.id)
        gw: Gateway = self.gateway_factory()
        backend = McpBackend(gw, task_id, str(task.requested_by))
        ctx = CloudCtx(
            task_id=task_id,
            bundle=bundle,
            host=None,  # type: ignore[arg-type]
            budget=budget,
            runner=runner,
            allowed_uris=[GUIDANCE_URI],
            enable_action=bool(bundle.business_policy.followup_allowed),
        )
        ctx.host = ToolHost(
            backend, budget, task_id, allowed_resource_uris={GUIDANCE_URI}, on_event=lambda t, d: ctx.emit(t, d)
        )

        async def on_events(batch: list[dict[str, Any]]) -> None:
            await save_events(task_id, batch)

        async def on_report(answer: dict[str, Any]) -> None:
            answer["model"] = model or None
            async with session_scope() as db:
                await db.execute(
                    update(CloudTask)
                    .where(CloudTask.id == task.id)
                    .values(answer=answer, report_version=CloudTask.report_version + 1, model_version=model or None)
                )

        ctx.on_events, ctx.on_report = on_events, on_report
        return ctx

    # ---- 실행 ----
    async def start(self, task_id: str) -> str:
        async with session_scope() as db:
            task = await db.get(CloudTask, uuid.UUID(task_id))
            if task is None:
                raise AppError(ErrorCode.NOT_FOUND, f"cloud task {task_id} not found")
            if task.status in TERMINAL or task.status == "WAITING_APPROVAL":
                log.info("cloud task %s already %s; skip", task_id, task.status)
                return task.status
            task.status = "RUNNING"
            budget_cfg = CloudBudget.from_config()
            task.deadline_at = deadline_for(budget_cfg.max_active_s)
            db.expunge(task)
        ctx = self._ctx(task)
        ctx.seq = task.last_event_seq  # 재전달 시 번호 이어쓰기 (이미 저장된 번호는 유일 제약으로 무시)
        ctx.flushed = len(ctx.events)
        res = await run_cloud_audit(ctx, "B", self.checkpointer)
        return await self._settle(task, ctx, res)

    async def resume(self, task_id: str, approval_id: str) -> str:
        async with session_scope() as db:
            task = await db.get(CloudTask, uuid.UUID(task_id))
            ap = await db.get(CloudApproval, uuid.UUID(approval_id))
            if task is None or ap is None or ap.task_id != task.id:
                raise AppError(ErrorCode.NOT_FOUND, "task/approval not found")
            if task.status in TERMINAL:
                return task.status
            if ap.status == "PENDING":
                raise AppError(ErrorCode.CONFLICT, "approval not decided yet")
            if task.status == "RUNNING":  # 같은 approvalRef 중복 재개 차단 (이미 진행 중)
                log.info("cloud task %s already running; duplicate resume ignored", task_id)
                return task.status
            remaining = max(0.0, CloudBudget.from_config().max_active_s - task.active_elapsed_ms / 1000)
            task.status = "RUNNING"
            task.deadline_at = deadline_for(remaining)
            decision = {"decision": "APPROVE" if ap.status == "APPROVED" else "REJECT", "approval_ref": str(ap.id)}
            db.expunge(task)
        ctx = self._ctx(task)
        ctx.seq = task.last_event_seq
        res = await resume_cloud_audit(ctx, decision, self.checkpointer)
        return await self._settle(task, ctx, res)

    async def _settle(self, task: CloudTask, ctx: CloudCtx, res: dict[str, Any]) -> str:
        status = res["status"]
        failure = (res.get("failure") or {}).get("code")
        usage = res.get("usage", {})
        async with session_scope() as db:
            values: dict[str, Any] = {
                "status": status if status in ("WAITING_APPROVAL", "COMPLETED", "FAILED") else "COMPLETED",
                "active_elapsed_ms": int(usage.get("active_elapsed_ms", 0)),
                "failure_code": failure,
            }
            if status == "WAITING_APPROVAL" and res.get("action"):
                a = res["action"]
                await db.execute(
                    insert(CloudApproval)
                    .values(
                        task_id=task.id,
                        action_id=a["action_id"],
                        tool=a["tool"],
                        args=a["args"],
                        args_hash=a["args_hash"],
                    )
                    .on_conflict_do_nothing(constraint="uq_cloud_approval_action")
                )
                values["deadline_at"] = None  # 승인 대기는 실행 기한에서 제외
            if status == "FAILED":
                # 부분 결과와 사유를 보고서로 남긴다 (검증된 부분만)
                values["answer"] = {
                    "summary": "",
                    "findings": res.get("findings", []),
                    "failure": res.get("failure"),
                    "usage": usage,
                    "partial": True,
                }
            await db.execute(update(CloudTask).where(CloudTask.id == task.id).values(**values))
        return str(values["status"])


async def save_events(task_id: str, batch: list[dict[str, Any]]) -> None:
    if not batch:
        return
    async with session_scope() as db:
        await db.execute(
            insert(CloudTaskEvent)
            .values(
                [
                    {"task_id": uuid.UUID(task_id), "event_seq": e["eventSeq"], "type": e["type"], "data": e["data"]}
                    for e in batch
                ]
            )
            .on_conflict_do_nothing(constraint="uq_cloud_event_seq")
        )
        await db.execute(
            update(CloudTask)
            .where(CloudTask.id == uuid.UUID(task_id), CloudTask.last_event_seq < max(e["eventSeq"] for e in batch))
            .values(last_event_seq=max(e["eventSeq"] for e in batch))
        )


async def reap_expired(now: datetime | None = None) -> list[str]:
    """실행 기한이 지난 RUNNING 작업을 FAILED(EXECUTION_DEADLINE_EXCEEDED) 로. 승인 대기 작업은 deadline 이 없다.
    자동 이어 실행하지 않는다 — 사용자는 새 taskId 로 재실행한다."""
    now = now or datetime.now(UTC)
    async with session_scope() as db:
        rows = (
            await db.execute(
                select(CloudTask.id).where(
                    CloudTask.status == "RUNNING", CloudTask.deadline_at.is_not(None), CloudTask.deadline_at < now
                )
            )
        ).scalars()
        ids = list(rows)
        for tid in ids:
            await db.execute(
                update(CloudTask)
                .where(CloudTask.id == tid, CloudTask.status == "RUNNING")
                .values(status="FAILED", failure_code="EXECUTION_DEADLINE_EXCEEDED")
            )
            seq = (await db.execute(select(CloudTask.last_event_seq).where(CloudTask.id == tid))).scalar_one() + 1
            await db.execute(
                insert(CloudTaskEvent)
                .values(
                    task_id=tid,
                    event_seq=seq,
                    type="FAILED",
                    data={"code": "EXECUTION_DEADLINE_EXCEEDED", "resumable": False},
                )
                .on_conflict_do_nothing(constraint="uq_cloud_event_seq")
            )
            await db.execute(update(CloudTask).where(CloudTask.id == tid).values(last_event_seq=seq))
    return [str(i) for i in ids]
