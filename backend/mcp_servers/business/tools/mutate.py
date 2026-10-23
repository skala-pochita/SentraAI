"""business-mcp 변경 도구 (명세 9.3 '변경' 중 읽기 전용 감사에 남은 것). 역할은 서버에서 X-Actor-Role 로 다시 검사한다.

수정·승인·병합·위험 수용 도구는 2026-10-21 삭제 (DECISIONS). 도구 게이트가 이 이름들을 계속 차단한다."""

from __future__ import annotations

import uuid
from typing import Any

from mcp.server.fastmcp import Context
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from core.db.models import Audit, AuditTarget, Finding, ProbeRun, Report, RiskPath
from core.db.session import session_scope
from core.errors import AppError, ErrorCode
from core.path_validation import validate_path
from core.schemas import FindingDraft, finding_fingerprint
from harness.masking import mask_obj, mask_text
from mcp_servers.business.app import mcp
from mcp_servers.common import current_call, mcp_tool

# Finding 상태 전이 (명세 9.3)
TRANSITIONS: dict[str, set[str]] = {
    "OPEN": {"FIXING", "ACCEPTED_RISK", "FALSE_POSITIVE"},
    "FIXING": {"RESOLVED", "MANUAL_REQUIRED"},
    "RESOLVED": {"OPEN"},
    "ACCEPTED_RISK": set(),
    "FALSE_POSITIVE": set(),
    "MANUAL_REQUIRED": set(),
}


def _uuid(v: str, name: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(v))
    except ValueError as e:
        raise AppError(ErrorCode.VALIDATION, f"{name} must be a UUID", {"value": str(v)[:60]}) from e


def _actor() -> uuid.UUID:
    call = current_call()
    if not call.actor_id:
        raise AppError(ErrorCode.VALIDATION, "X-Actor-Id header required for mutating tools")
    return _uuid(call.actor_id, "X-Actor-Id")


async def _ensure_audit_writer(db: Any, aid: uuid.UUID, target_id: uuid.UUID | None = None) -> None:
    call = current_call()
    if call.role == "ADMIN":
        return
    audit = await db.get(Audit, aid)
    if audit is None or str(audit.requested_by) != call.actor_id:
        raise AppError(ErrorCode.FORBIDDEN, "다른 사용자의 감사 결과를 저장할 수 없습니다.")
    if target_id is not None:
        linked = await db.get(AuditTarget, (aid, target_id))
        if linked is None:
            raise AppError(ErrorCode.FORBIDDEN, "감사에 등록되지 않은 대상입니다.")


@mcp_tool(mcp, roles=("ADMIN", "DEVELOPER"))
async def create_finding(audit_id: str, finding: dict[str, Any], ctx: Context | None = None) -> Any:
    """FindingDraft 를 저장한다. fingerprint = hash(target, category, path, normalized_snippet) 중복이면 기존 ID 반환 (멱등).

    source=correlation 이면 component_ids(구성 finding_id 목록)로 risk_paths 도 저장한다.
    입력: audit_id, finding (FindingDraft)
    출력: {finding_id, created}
    권한: ADMIN. 오류: VALIDATION(스키마). 부작용: findings(+risk_paths) 쓰기.
    """
    try:
        f = FindingDraft.model_validate(mask_obj(finding))
    except ValueError as e:
        raise AppError(ErrorCode.VALIDATION, f"invalid FindingDraft: {str(e)[:300]}") from e
    aid = _uuid(audit_id, "audit_id")
    fp = finding_fingerprint(f)
    async with session_scope() as db:
        await _ensure_audit_writer(db, aid, _uuid(f.target_id, "target_id"))
        if f.attack_path:
            await validate_path(db, aid, f.attack_path)
            for target in f.attack_path.target_ids:
                await _ensure_audit_writer(db, aid, _uuid(target, "target_id"))
        for cid in f.component_ids:
            component = await db.get(Finding, _uuid(cid, "component_id"))
            if component is None or component.audit_id != aid:
                raise AppError(ErrorCode.VALIDATION, "component outside audit")
        stmt = (
            insert(Finding)
            .values(
                audit_id=aid,
                target_id=_uuid(f.target_id, "target_id"),
                source=f.source,
                category=f.category,
                rule_id=f.rule_id,
                title=f.title,
                severity=f.severity,
                confidence=f.confidence,
                fingerprint=fp,
                subject=f.subject,
                evidence=[e.model_dump() for e in f.evidence],
                reasoning_summary=f.reasoning_summary,
                tools_used=f.tools_used,
                standard_refs=f.standard_refs,
                fix_hint=f.fix_hint,
            )
            .on_conflict_do_nothing(index_elements=["audit_id", "fingerprint"])
            .returning(Finding.id)
        )
        new_id = (await db.execute(stmt)).scalar_one_or_none()
        created = new_id is not None
        if new_id is None:
            new_id = (
                await db.execute(select(Finding.id).where(Finding.audit_id == aid, Finding.fingerprint == fp))
            ).scalar_one()
        if f.source == "correlation":
            comps = [_uuid(c, "component_id") for c in f.component_ids]
            path = (
                await db.execute(select(RiskPath).where(RiskPath.correlation_finding_id == new_id))
            ).scalar_one_or_none()
            payload = f.attack_path.model_dump() if f.attack_path else None
            if path is None:
                path = RiskPath(
                    audit_id=aid,
                    correlation_finding_id=new_id,
                    rule_id=f.rule_id or "CORR",
                    severity=f.severity,
                    title=f.title,
                    finding_ids=[new_id, *comps],
                    explanation=f.reasoning_summary,
                    payload=payload,
                )
                db.add(path)
            elif payload:
                # Replayed work may add observations; it must never discard earlier references.
                old = path.payload or {}
                for key in ("steps", "edges"):
                    previous = {x["id"]: x for x in old.get(key, [])}
                    for item in payload[key]:
                        for evidence in previous.get(item["id"], {}).get("evidence_refs", []):
                            if evidence not in item["evidence_refs"]:
                                item["evidence_refs"].append(evidence)
                for reason in old.get("code_reasons", []):
                    if reason not in payload.get("code_reasons", []):
                        payload.setdefault("code_reasons", []).append(reason)
                path.payload = payload
        result: dict[str, Any] = {"finding_id": str(new_id), "created": created}
        if f.source == "correlation":
            await db.flush()
            assert path is not None
            result.update(path_payload=path.payload, risk_path_id=str(path.id))
        return result


@mcp_tool(mcp, roles=("ADMIN", "DEVELOPER"))
async def finalize_probe_verdict(probe_run_id: str, verdict: str, ctx: Context | None = None) -> Any:
    """Persist a final LLM judgement only for an undetermined run in the current audit."""
    if verdict not in {"SUCCESS", "BLOCKED"}:
        raise AppError(ErrorCode.VALIDATION, "invalid final verdict")
    async with session_scope() as db:
        row = (
            await db.execute(
                select(ProbeRun).where(ProbeRun.id == _uuid(probe_run_id, "probe_run_id")).with_for_update()
            )
        ).scalar_one_or_none()
        if row is None or row.audit_id is None or str(row.audit_id) != str(current_call().audit_id):
            raise AppError(ErrorCode.FORBIDDEN, "probe outside current audit")
        await _ensure_audit_writer(db, row.audit_id, row.target_id)
        if row.verdict == verdict and row.judged_by == "llm":
            return {"updated": False}
        if row.verdict != "UNDETERMINED":
            raise AppError(ErrorCode.CONFLICT, "only undetermined probes may be finalized")
        row.verdict = verdict
        row.success = verdict == "SUCCESS"
        row.judged_by = "llm"
        return {"updated": True}


@mcp_tool(mcp, roles=("ADMIN", "DEVELOPER"))
async def update_finding_status(finding_id: str, status: str, reason: str = "", ctx: Context | None = None) -> Any:
    """Finding 상태를 전이한다. 전이 표: OPEN→FIXING→RESOLVED, OPEN→ACCEPTED_RISK, OPEN→FALSE_POSITIVE,
    FIXING→MANUAL_REQUIRED, RESOLVED→OPEN(재발). 같은 상태로의 요청은 그대로 성공(멱등).

    입력: finding_id, status, reason
    출력: {finding_id, status, previous}
    권한: ADMIN, DEVELOPER(FIXING 만 — 대상 소유 검사는 API 계층)
    오류: NOT_FOUND, CONFLICT(전이 표 위반), FORBIDDEN. 부작용: findings.status 변경.
    """
    call = current_call()
    if call.role == "DEVELOPER" and status != "FIXING":
        raise AppError(ErrorCode.FORBIDDEN, "DEVELOPER can only set FIXING")
    async with session_scope() as db:
        f = await db.get(Finding, _uuid(finding_id, "finding_id"))
        if f is None:
            raise AppError(ErrorCode.NOT_FOUND, "finding not found")
        prev = f.status
        if prev != status and status not in TRANSITIONS.get(prev, set()):
            raise AppError(
                ErrorCode.CONFLICT,
                f"invalid transition {prev} → {status}",
                {"allowed": sorted(TRANSITIONS.get(prev, set()))},
            )
        f.status = status
        return {"finding_id": finding_id, "status": status, "previous": prev}


@mcp_tool(mcp, roles=("ADMIN", "DEVELOPER"))
async def save_report(audit_id: str, report_json: dict[str, Any], report_md: str, ctx: Context | None = None) -> Any:
    """감사 보고서를 저장한다 (감사당 1개, 재호출 시 갱신). 저장 전 비밀값을 다시 마스킹한다.

    입력: audit_id, report_json, report_md
    출력: {report_id}
    권한: ADMIN. 오류: VALIDATION. 부작용: reports upsert.
    """
    aid = _uuid(audit_id, "audit_id")
    from core.compliance import seal_report

    body_json = seal_report(report_json)
    body_md = mask_text(report_md)
    async with session_scope() as db:
        await _ensure_audit_writer(db, aid)
        stmt = (
            insert(Report)
            .values(audit_id=aid, report_json=body_json, report_md=body_md)
            .on_conflict_do_update(index_elements=["audit_id"], set_={"report_json": body_json, "report_md": body_md})
            .returning(Report.id)
        )
        rid = (await db.execute(stmt)).scalar_one()
        return {"report_id": str(rid)}
