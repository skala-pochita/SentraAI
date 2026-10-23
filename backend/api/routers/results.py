"""감사 결과 조회 (완료 후에만): Findings·결합 위험·probe 매트릭스·보고서·트레이스."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import Admin, CurrentUser, LoggedIn, ensure_target_access, require_roles
from api.routers.audits import _iso, require_completed
from core.compliance import render_compliance, verify_report
from core.db.models import Finding, ProbeRun, Report, RiskPath, TraceSpan
from core.db.session import get_db
from core.errors import AppError, ErrorCode
from core.mcp_client import CallCtx, http_gateway

router = APIRouter(tags=["audits"])


# ---------------- Findings ----------------


def finding_out(f: Finding) -> dict[str, Any]:
    return {
        "id": str(f.id),
        "audit_id": str(f.audit_id),
        "target_id": str(f.target_id),
        "source": f.source,
        "category": f.category,
        "rule_id": f.rule_id,
        "title": f.title,
        "severity": f.severity,
        "confidence": f.confidence,
        "status": f.status,
        "evidence": f.evidence,
        "reasoning_summary": f.reasoning_summary,
        "tools_used": f.tools_used,
        "standard_refs": f.standard_refs,
        "fix_hint": f.fix_hint,
        "created_at": _iso(f.created_at),
    }


SEV_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}


@router.get("/audits/{audit_id}/findings")
async def audit_findings(
    audit_id: uuid.UUID,
    severity: str | None = None,
    status: str | None = None,
    source: str | None = None,
    user: CurrentUser = LoggedIn,
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    await require_completed(db, user, audit_id)
    q = select(Finding).where(Finding.audit_id == audit_id)
    for col, val in ((Finding.severity, severity), (Finding.status, status), (Finding.source, source)):
        if val:
            q = q.where(col == val)
    rows = [finding_out(f) for f in (await db.execute(q)).scalars()]
    rows.sort(key=lambda f: (SEV_RANK[f["severity"]], -f["confidence"]))  # 심각도 → 신뢰도
    return rows


@router.get("/findings/{finding_id}")
async def get_finding(
    finding_id: uuid.UUID, user: CurrentUser = LoggedIn, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    f = await db.get(Finding, finding_id)
    if f is None:
        raise AppError(ErrorCode.NOT_FOUND, "finding not found")
    await ensure_target_access(user, db, {f.target_id})
    await require_completed(db, user, f.audit_id)
    out = finding_out(f)
    probe_ids = [e.get("probe_transcript_ref") for e in f.evidence if e.get("probe_transcript_ref")]
    if probe_ids:
        runs = (await db.execute(select(ProbeRun).where(ProbeRun.id.in_([uuid.UUID(x) for x in probe_ids])))).scalars()
        out["probe_runs"] = [probe_out(r) for r in runs]
    paths = (await db.execute(select(RiskPath).where(RiskPath.audit_id == f.audit_id))).scalars()
    out["risk_paths"] = [risk_out(r) for r in paths if f.id in r.finding_ids]
    stored = (await db.execute(select(Report).where(Report.audit_id == f.audit_id))).scalar_one_or_none()
    saved = (
        next((item for item in stored.report_json.get("findings", []) if item.get("finding_id") == str(f.id)), {})
        if stored
        else {}
    )
    out["compliance_refs"] = saved.get("compliance_refs", [])
    return out


class FindingPatch(BaseModel):
    status: Literal["FIXING", "OPEN", "ACCEPTED_RISK"] | None = None
    false_positive: dict[str, str] | None = None  # {"reason": "..."}


@router.patch("/findings/{finding_id}")
async def patch_finding(
    finding_id: uuid.UUID,
    body: FindingPatch,
    user: CurrentUser = Depends(require_roles("ADMIN", "DEVELOPER")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    f = await db.get(Finding, finding_id)
    if f is None:
        raise AppError(ErrorCode.NOT_FOUND, "finding not found")
    await ensure_target_access(user, db, {f.target_id})
    gw = http_gateway()
    ctx = CallCtx("api", user.role, str(user.id), str(f.audit_id))
    if body.false_positive is not None:
        reason = (body.false_positive or {}).get("reason", "").strip()
        if not reason:
            raise AppError(ErrorCode.VALIDATION, "false_positive.reason required")
        return dict(await gw.call("register_false_positive", {"finding_id": str(f.id), "reason": reason}, ctx))
    if body.status:
        if user.role == "DEVELOPER" and body.status != "FIXING":
            raise AppError(ErrorCode.FORBIDDEN, "DEVELOPER can only set FIXING")
        return dict(
            await gw.call(
                "update_finding_status",
                {"finding_id": str(f.id), "status": body.status, "reason": f"by {user.email}"},
                ctx,
            )
        )
    raise AppError(ErrorCode.VALIDATION, "status or false_positive required")


def risk_out(r: RiskPath) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "rule_id": r.rule_id,
        "severity": r.severity,
        "title": r.title,
        "finding_ids": [str(x) for x in r.finding_ids],
        "explanation": r.explanation,
    }


@router.get("/audits/{audit_id}/risk-paths")
async def risk_paths(
    audit_id: uuid.UUID, user: CurrentUser = LoggedIn, db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    await require_completed(db, user, audit_id)
    rows = (await db.execute(select(RiskPath).where(RiskPath.audit_id == audit_id))).scalars()
    return [risk_out(r) for r in rows]


def probe_out(r: ProbeRun) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "probe_id": r.probe_id,
        "category": r.category,
        "channel": r.channel,
        "attempt": r.attempt,
        "request": r.request,
        "reply_masked": r.reply_masked,
        "tool_calls": r.tool_calls,
        "success": r.success,
        "judged_by": r.judged_by,
        "latency_ms": r.latency_ms,
        "created_at": _iso(r.created_at),
    }


@router.get("/audits/{audit_id}/probes")
async def probes(
    audit_id: uuid.UUID, user: CurrentUser = LoggedIn, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    """probe × 시도 매트릭스."""
    await require_completed(db, user, audit_id)
    rows = list(
        (
            await db.execute(select(ProbeRun).where(ProbeRun.audit_id == audit_id).order_by(ProbeRun.created_at))
        ).scalars()
    )
    matrix: dict[str, dict[str, Any]] = {}
    for r in rows:
        m = matrix.setdefault(
            r.probe_id, {"probe_id": r.probe_id, "category": r.category, "channel": r.channel, "runs": []}
        )
        m["runs"].append(probe_out(r))
    cats: dict[str, dict[str, int]] = {}
    for m in matrix.values():
        c = cats.setdefault(m["category"], {"attempts": 0, "successes": 0})
        c["attempts"] += len(m["runs"])
        c["successes"] += sum(1 for x in m["runs"] if x["success"])
    report = (await db.execute(select(Report).where(Report.audit_id == audit_id))).scalar_one_or_none()
    return {
        "coverage": report.report_json.get("agent_coverage", []) if report else [],
        "probes": list(matrix.values()),
        "asr_by_category": {
            k: round(v["successes"] / v["attempts"], 3) if v["attempts"] else 0 for k, v in cats.items()
        },
    }


@router.get("/audits/{audit_id}/report")
async def report_json(
    audit_id: uuid.UUID, user: CurrentUser = LoggedIn, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    await require_completed(db, user, audit_id)
    r = (await db.execute(select(Report).where(Report.audit_id == audit_id))).scalar_one_or_none()
    if r is None:
        raise AppError(ErrorCode.NOT_FOUND, "report not ready")
    return {"report_id": str(r.id), "created_at": _iso(r.created_at), **r.report_json}


@router.get("/audits/{audit_id}/report.md", response_class=PlainTextResponse)
async def report_md(
    audit_id: uuid.UUID, user: CurrentUser = LoggedIn, db: AsyncSession = Depends(get_db)
) -> PlainTextResponse:
    await require_completed(db, user, audit_id)
    r = (await db.execute(select(Report).where(Report.audit_id == audit_id))).scalar_one_or_none()
    if r is None:
        raise AppError(ErrorCode.NOT_FOUND, "report not ready")
    return PlainTextResponse(
        r.report_md,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="audit-{str(audit_id)[:8]}.md"'},
    )


@router.get("/traces")
async def traces(
    audit_id: uuid.UUID | None = None,
    eval_run_id: uuid.UUID | None = None,
    _: CurrentUser = Admin,
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    if not audit_id and not eval_run_id:
        raise AppError(ErrorCode.VALIDATION, "audit_id or eval_run_id required")
    q = select(TraceSpan).order_by(TraceSpan.started_at)
    q = q.where(TraceSpan.audit_id == audit_id) if audit_id else q.where(TraceSpan.eval_run_id == eval_run_id)
    return [
        {
            "id": str(s.id),
            "parent_id": str(s.parent_id) if s.parent_id else None,
            "kind": s.kind,
            "name": s.name,
            "model": s.model,
            "prompt_id": s.prompt_id,
            "input_tokens": s.input_tokens,
            "output_tokens": s.output_tokens,
            "cost_usd": float(s.cost_usd or 0),
            "latency_ms": s.latency_ms,
            "status": s.status,
            "error_code": s.error_code,
            "trace_id": s.trace_id,
            "started_at": _iso(s.started_at),
        }
        for s in (await db.execute(q.limit(5000))).scalars()
    ]


async def _compliance_snapshot(audit_id: uuid.UUID, user: CurrentUser, db: AsyncSession) -> dict[str, Any]:
    await require_completed(db, user, audit_id)
    stored = (await db.execute(select(Report).where(Report.audit_id == audit_id))).scalar_one_or_none()
    if stored is None:
        raise AppError(ErrorCode.NOT_FOUND, "report not ready")
    section = stored.report_json.get("compliance") or {}
    if section.get("integrity") and not verify_report(stored.report_json):
        raise AppError(ErrorCode.VALIDATION, "stored report integrity mismatch")
    return dict(stored.report_json)


@router.get("/audits/{audit_id}/compliance")
async def compliance_json(
    audit_id: uuid.UUID,
    user: CurrentUser = Depends(require_roles("ADMIN", "AUDITOR")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    report = await _compliance_snapshot(audit_id, user, db)
    return {
        "audit_id": report.get("audit_id"),
        "generated_at": report.get("generated_at"),
        "targets": report.get("targets", []),
        "agent_coverage": report.get("agent_coverage", []),
        "eval_config": report.get("eval_config"),
        "report_snapshot": report,
        **report.get("compliance", {"schema": 0, "status": "NOT_ASSESSED"}),
    }


@router.get("/audits/{audit_id}/compliance.md", response_class=PlainTextResponse)
async def compliance_md(
    audit_id: uuid.UUID,
    user: CurrentUser = Depends(require_roles("ADMIN", "AUDITOR")),
    db: AsyncSession = Depends(get_db),
) -> PlainTextResponse:
    report = await _compliance_snapshot(audit_id, user, db)
    return PlainTextResponse(
        render_compliance(report),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="compliance-{str(audit_id)[:8]}.md"'},
    )
