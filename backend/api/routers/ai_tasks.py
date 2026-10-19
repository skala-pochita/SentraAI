"""/api/ai/tasks 5개 API (기획서 6장 외부 API; DECISIONS #12). 202 는 접수이며 감사·티켓 실행 완료가 아니다.

권한: 작업 생성 ADMIN/DEVELOPER, 승인 ADMIN 만. 조회·스트림은 소유자 또는 ADMIN (다른 사용자의 작업은 404 로 숨긴다).
이벤트 필드: taskId, eventSeq, type, timestamp, data, trace_id — type 은 TOKEN/TOOL_START/TOOL_END/APPROVAL_REQUIRED/DONE/FAILED.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from api.deps import CurrentUser, LoggedIn, require_roles
from core.cloud.bundle import parse_bundle
from core.db.models import CloudApproval, CloudTask, CloudTaskEvent
from core.db.session import get_db, session_scope
from core.errors import AppError, ErrorCode
from core.kafka.envelope import Envelope
from core.kafka.producer import publish

router = APIRouter(prefix="/ai", tags=["ai-tasks"])
Requester = Depends(require_roles("ADMIN", "DEVELOPER"))
Approver = Depends(require_roles("ADMIN"))
PUBLIC_EVENTS = {"TOKEN", "TOOL_START", "TOOL_END", "APPROVAL_REQUIRED", "DONE", "FAILED"}


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


class CreateTask(BaseModel):
    evidence_bundle: dict[str, Any]
    attachments: list[dict[str, str]] = Field(default_factory=list, max_length=20)  # 근거 원문 첨부 {name, content}


async def _get_task(db: AsyncSession, user: CurrentUser, task_id: str) -> CloudTask:
    try:
        tid = uuid.UUID(task_id)
    except ValueError as e:
        raise AppError(ErrorCode.NOT_FOUND, "task not found") from e
    task = await db.get(CloudTask, tid)
    if task is None or (not user.is_admin and task.requested_by != user.id):
        raise AppError(ErrorCode.NOT_FOUND, "task not found")  # 존재 여부도 숨긴다
    return task


@router.post("/tasks", status_code=202)
async def create_task(
    body: CreateTask, user: CurrentUser = Requester, db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    bundle = parse_bundle(body.evidence_bundle)  # 스키마·참조·금지 필드 검증 (실패 시 422)
    raw = json.dumps(body.evidence_bundle, sort_keys=True, ensure_ascii=False)
    task = CloudTask(
        requested_by=user.id,
        bundle_id=bundle.bundle_id,
        bundle=body.evidence_bundle,
        bundle_sha256=hashlib.sha256(raw.encode()).hexdigest(),
        status="QUEUED",
    )
    db.add(task)
    await db.commit()
    await publish(
        "audit.requested",
        str(task.id),
        Envelope(type="audit.requested", payload={"task_id": str(task.id), "requested_by": str(user.id)}),
    )
    return {"taskId": str(task.id)}


def _answer(task: CloudTask, pending: CloudApproval | None) -> dict[str, Any] | None:
    answer = dict(task.answer) if task.answer else None
    if task.status == "WAITING_APPROVAL" and pending is not None:
        answer = answer or {}
        answer["pendingAction"] = {
            "actionId": pending.action_id,
            "tool": pending.tool,
            "args": pending.args,
            "target": pending.args.get("target"),
            "argsHash": pending.args_hash,
        }
    return answer


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: str, user: CurrentUser = LoggedIn, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    task = await _get_task(db, user, task_id)
    pending = (
        await db.execute(
            select(CloudApproval).where(CloudApproval.task_id == task.id, CloudApproval.status == "PENDING")
        )
    ).scalar_one_or_none()
    answer = _answer(task, pending)
    sources = list((answer or {}).get("sources", [])) if answer else []
    return {
        "status": task.status,
        "answer": answer,
        "sources": sources,
        "taskId": str(task.id),
        "reportVersion": task.report_version,
        "failureCode": task.failure_code,
    }


@router.get("/tasks")
async def list_tasks(
    mine: bool = True, user: CurrentUser = LoggedIn, db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    q = select(CloudTask).order_by(CloudTask.created_at.desc()).limit(100)
    if mine or not user.is_admin:
        q = q.where(CloudTask.requested_by == user.id)
    rows = (await db.execute(q)).scalars()
    return [
        {"taskId": str(t.id), "status": t.status, "createdAt": _iso(t.created_at), "updatedAt": _iso(t.updated_at)}
        for t in rows
    ]


async def _event_stream(request: Request, task_id: uuid.UUID, after: int) -> AsyncIterator[dict[str, Any]]:
    cursor = after
    while not await request.is_disconnected():
        async with session_scope() as db:
            rows = list(
                (
                    await db.execute(
                        select(CloudTaskEvent)
                        .where(CloudTaskEvent.task_id == task_id, CloudTaskEvent.event_seq > cursor)
                        .order_by(CloudTaskEvent.event_seq)
                    )
                ).scalars()
            )
            task = await db.get(CloudTask, task_id)
        for e in rows:
            cursor = e.event_seq
            if e.type not in PUBLIC_EVENTS:
                continue
            yield {
                "id": str(e.event_seq),
                "event": e.type,
                "data": json.dumps(
                    {
                        "taskId": str(task_id),
                        "eventSeq": e.event_seq,
                        "type": e.type,
                        "timestamp": _iso(e.ts),
                        "data": e.data,
                        "trace_id": task.trace_id if task else None,
                    },
                    ensure_ascii=False,
                ),
            }
            if e.type in ("DONE", "FAILED"):
                return
        if task is None or (task.status in ("COMPLETED", "FAILED") and not rows):
            return
        await asyncio.sleep(0.5)


@router.get("/tasks/{task_id}/stream")
async def stream_task(
    request: Request,
    task_id: str,
    after: int = Query(default=0, ge=0, description="이 eventSeq 이후만 받는다 (과거 토큰 재전송은 지원하지 않음)"),
    user: CurrentUser = LoggedIn,
    db: AsyncSession = Depends(get_db),
) -> EventSourceResponse:
    task = await _get_task(db, user, task_id)
    return EventSourceResponse(_event_stream(request, task.id, after), ping=15)


class Decision(BaseModel):
    actionId: str
    decision: Literal["APPROVE", "REJECT"]
    argsHash: str


@router.post("/tasks/{task_id}/approval", status_code=202)
async def decide(
    task_id: str, body: Decision, user: CurrentUser = Approver, db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    task = await _get_task(db, user, task_id)
    ap = (
        await db.execute(
            select(CloudApproval).where(CloudApproval.task_id == task.id, CloudApproval.action_id == body.actionId)
        )
    ).scalar_one_or_none()
    if ap is None:
        raise AppError(ErrorCode.NOT_FOUND, "no such pending action")
    if ap.args_hash != body.argsHash:  # 승인 화면에서 검토한 인수와 서버에 고정된 인수가 달라지면 거부
        raise AppError(ErrorCode.CONFLICT, "argsHash does not match the pending action", {"expected_changed": True})
    wanted = "APPROVED" if body.decision == "APPROVE" else "REJECTED"
    if ap.status == "PENDING":
        if task.status != "WAITING_APPROVAL":
            raise AppError(ErrorCode.CONFLICT, f"task is {task.status}, not waiting for approval")
        ap.status, ap.decided_by, ap.decided_at = wanted, user.id, datetime_now()
        await db.commit()
    elif ap.status != wanted:
        raise AppError(ErrorCode.CONFLICT, f"already {ap.status}")
    # 같은 결정의 재전송은 resume 만 다시 보낸다 (실행 쪽이 같은 approvalRef 중복을 차단)
    await publish(
        "audit.resume",
        str(task.id),
        Envelope(
            type="audit.resume",
            payload={"task_id": str(task.id), "kind": "cloud_approval", "approval_id": str(ap.id)},
        ),
    )
    return {"approvalRef": str(ap.id), "status": ap.status}


def datetime_now() -> datetime:
    from datetime import UTC

    return datetime.now(UTC)
