"""business-mcp Agent·Cloud 도구 (기획서 6장 MCP 도구 계약).

- get_audit_evidence: 작업의 구조화 증거 조회 (소유자 또는 ADMIN/AUDITOR 만)
- get_remediation_status: 작업의 티켓 상태 조회
- create_remediation_ticket: 승인(cloud_approvals)·인수 해시·(task_id, action_id) 멱등을 DB 에서 검증한 뒤 티켓 생성
  모델이 만든 승인은 무효다: approval_ref 는 사람이 결정한 APPROVED 레코드여야 한다.
"""

from __future__ import annotations

import uuid
from typing import Any

from mcp.server.fastmcp import Context
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from core.cloud.backend_mem import MemoryBackend
from core.cloud.bundle import parse_bundle
from core.cloud.tickets import args_hash
from core.db.models import CloudApproval, CloudTask, RemediationTicket
from core.db.session import session_scope
from core.errors import AppError, ErrorCode
from mcp_servers.business.app import mcp
from mcp_servers.common import current_call, mcp_tool


def _uuid(v: str, name: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(v))
    except ValueError as e:
        raise AppError(ErrorCode.VALIDATION, f"{name} must be a UUID") from e


async def _task_for_actor(db: Any, task_id: str) -> CloudTask:
    task = await db.get(CloudTask, _uuid(task_id, "task_id"))
    if task is None:
        raise AppError(ErrorCode.NOT_FOUND, "task not found")
    call = current_call()
    if call.role not in ("ADMIN", "AUDITOR") and str(task.requested_by) != (call.actor_id or ""):
        raise AppError(ErrorCode.FORBIDDEN, "task belongs to another user")
    return task


@mcp_tool(mcp)
async def get_audit_evidence(
    task_id: str, evidence_ids: list[str] | None = None, kinds: list[str] | None = None, ctx: Context | None = None
) -> Any:
    """작업의 정규화 증거 묶음에서 구조화 증거(도구·주체·정책·자원·매핑)를 조회한다. 여러 ID 를 한 번에 요청한다.

    입력: task_id, evidence_ids (선택), kinds (선택: tool|principal|policy|resource|mapping)
    출력: {bundle_id, items[{kind, evidence_id, source_ref, ...}], not_found, business_policy, missing_evidence}
    권한: 작업 소유자 또는 ADMIN/AUDITOR. 오류: NOT_FOUND, FORBIDDEN, VALIDATION. 부작용: 없음.
    """
    async with session_scope() as db:
        task = await _task_for_actor(db, task_id)
        bundle = parse_bundle(task.bundle)
    return await MemoryBackend(bundle).get_audit_evidence(evidence_ids, kinds)


@mcp_tool(mcp)
async def get_remediation_status(task_id: str, ctx: Context | None = None) -> Any:
    """작업의 수정 요청 티켓과 승인 상태를 조회한다.

    입력: task_id
    출력: {tickets[{ticket_id, action_id, title, target}], approvals[{action_id, status}]}
    권한: 작업 소유자 또는 ADMIN/AUDITOR. 오류: NOT_FOUND, FORBIDDEN. 부작용: 없음.
    """
    async with session_scope() as db:
        task = await _task_for_actor(db, task_id)
        tickets = (await db.execute(select(RemediationTicket).where(RemediationTicket.task_id == task.id))).scalars()
        approvals = (await db.execute(select(CloudApproval).where(CloudApproval.task_id == task.id))).scalars()
        return {
            "tickets": [
                {"ticket_id": str(t.id), "action_id": t.action_id, "title": t.title, "target": t.target}
                for t in tickets
            ],
            "approvals": [{"action_id": a.action_id, "status": a.status} for a in approvals],
        }


@mcp_tool(mcp, roles=("ADMIN",))
async def create_remediation_ticket(
    task_id: str,
    action_id: str,
    args: dict[str, Any],
    approval_ref: str | None = None,
    args_hash_claimed: str | None = None,
    ctx: Context | None = None,
) -> Any:
    """승인된 수정 요청 티켓을 제품 내부에 생성한다 (외부 Jira·이메일·운영 설정은 변경하지 않음).

    서버가 검증: ① approval_ref 가 같은 task_id·action_id 의 APPROVED 레코드 ② 현재 인수의 SHA-256 이 승인된 해시와 동일
    ③ (task_id, action_id) 유일 — 같은 인수의 재시도는 기존 티켓 반환, 다른 인수는 CONFLICT.
    입력: task_id, action_id, args {title, target, body}, approval_ref (승인 ID), args_hash_claimed (선택)
    출력: {ticket_id, task_id, action_id, args_hash, title, target, duplicate}
    권한: ADMIN. 오류: FORBIDDEN(승인 없음·불일치), CONFLICT, NOT_FOUND, VALIDATION. 부작용: remediation_tickets 쓰기.
    """
    tid = _uuid(task_id, "task_id")
    h = args_hash(args)
    for k in ("title", "target"):
        if not str(args.get(k, "")).strip():
            raise AppError(ErrorCode.VALIDATION, f"args.{k} is required")
    async with session_scope() as db:
        existing = (
            await db.execute(
                select(RemediationTicket).where(
                    RemediationTicket.task_id == tid, RemediationTicket.action_id == action_id
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.args_hash != h:
                raise AppError(ErrorCode.CONFLICT, "same actionId with different arguments")
            return _ticket_out(existing, duplicate=True)
        if not approval_ref:
            raise AppError(ErrorCode.FORBIDDEN, "no valid approval for this change")
        ap = await db.get(CloudApproval, _uuid(approval_ref, "approval_ref"))
        if ap is None or ap.status != "APPROVED":
            raise AppError(ErrorCode.FORBIDDEN, "no valid approval for this change")
        if ap.task_id != tid or ap.action_id != action_id:
            raise AppError(ErrorCode.FORBIDDEN, "approval does not belong to this action")
        if ap.args_hash != h or (args_hash_claimed is not None and args_hash_claimed != h):
            raise AppError(ErrorCode.FORBIDDEN, "arguments differ from the approved ones")
        stmt = (
            insert(RemediationTicket)
            .values(
                task_id=tid,
                action_id=action_id,
                args_hash=h,
                approval_id=ap.id,
                title=str(args["title"]),
                target=str(args["target"]),
                body=str(args.get("body", "")),
            )
            .on_conflict_do_nothing(constraint="uq_ticket_action")
            .returning(RemediationTicket.id)
        )
        created = (await db.execute(stmt)).scalar_one_or_none()
        row = (
            await db.execute(
                select(RemediationTicket).where(
                    RemediationTicket.task_id == tid, RemediationTicket.action_id == action_id
                )
            )
        ).scalar_one()
        return _ticket_out(row, duplicate=created is None)


def _ticket_out(t: RemediationTicket, duplicate: bool) -> dict[str, Any]:
    return {
        "ticket_id": str(t.id),
        "task_id": str(t.task_id),
        "action_id": t.action_id,
        "args_hash": t.args_hash,
        "title": t.title,
        "target": t.target,
        "duplicate": duplicate,
    }
