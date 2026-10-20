"""Read-only audits, findings, reports, traces and progress SSE."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from api.deps import Admin, CurrentUser, LoggedIn, accessible_target_ids, ensure_target_access, require_roles
from api.errors import Page, page_params, paged
from core.db.models import (
    Audit,
    AuditEvent,
    AuditTarget,
    Finding,
    ProbeRun,
    Report,
    RiskPath,
    Target,
    TraceSpan,
)
from core.db.session import get_db
from core.errors import AppError, ErrorCode
from core.kafka.envelope import Envelope
from core.kafka.producer import publish
from core.mcp_client import CallCtx, http_gateway
from core.redis.client import get_redis
from harness.observability import trace_id_for

router = APIRouter(tags=["audits"])


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def audit_out(a: Audit, target_ids: list[str], live: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "id": str(a.id),
        "status": a.status,
        "mode": a.mode,
        "eval_config": a.eval_config,
        "requested_by": str(a.requested_by),
        "thread_id": a.thread_id,
        "current_node": (live or {}).get("node") or a.current_node,
        "progress": float((live or {}).get("progress") or (1.0 if a.status == "COMPLETED" else 0.0)),
        "started_at": _iso(a.started_at),
        "finished_at": _iso(a.finished_at),
        "created_at": _iso(a.created_at),
        "summary": a.summary if a.status == "COMPLETED" else {},
        "target_ids": target_ids,
    }


async def _targets_of(db: AsyncSession, audit_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    rows = (await db.execute(select(AuditTarget).where(AuditTarget.audit_id.in_(audit_ids)))).scalars()
    out: dict[uuid.UUID, list[str]] = {}
    for r in rows:
        out.setdefault(r.audit_id, []).append(str(r.target_id))
    return out


async def load_audit(db: AsyncSession, user: CurrentUser, audit_id: uuid.UUID) -> tuple[Audit, list[str]]:
    a = await db.get(Audit, audit_id)
    if a is None:
        raise AppError(ErrorCode.NOT_FOUND, "audit not found")
    tids = (await _targets_of(db, [a.id])).get(a.id, [])
    await ensure_target_access(user, db, {uuid.UUID(t) for t in tids})
    return a, tids


async def require_completed(db: AsyncSession, user: CurrentUser, audit_id: uuid.UUID) -> None:
    audit, _ = await load_audit(db, user, audit_id)
    if audit.status != "COMPLETED":
        raise AppError(ErrorCode.CONFLICT, "감사 완료 후 결과를 확인할 수 있습니다.")


class AuditCreate(BaseModel):
    target_ids: list[uuid.UUID] = Field(min_length=1)
    mode: Literal["full", "code", "agent"] = "full"


@router.post("/audits", status_code=202)
async def create_audit(
    body: AuditCreate,
    user: CurrentUser = Depends(require_roles("ADMIN", "DEVELOPER")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    targets = list((await db.execute(select(Target).where(Target.id.in_(body.target_ids)))).scalars())
    if len(targets) != len(set(body.target_ids)):
        raise AppError(ErrorCode.NOT_FOUND, "some targets not found")
    await ensure_target_access(user, db, {t.id for t in targets})
    if body.mode == "full":
        linked = {t.linked_target_id for t in targets if t.config.get("source") == "upload" and t.linked_target_id}
        present = {t.id for t in targets}
        for tid in linked - present:
            other = await db.get(Target, tid)
            if other is not None:
                targets.append(other)
        await ensure_target_access(user, db, {t.id for t in targets})
    lock_ids = {t.id for t in targets} | {t.linked_target_id for t in targets if t.linked_target_id}
    await db.execute(select(Target.id).where(Target.id.in_(lock_ids)).order_by(Target.id).with_for_update())
    if body.mode in {"full", "agent"}:
        from pathlib import Path

        from core.execution_config import readiness, settings_for_agent

        for target in targets:
            if target.type == "agent" and target.config.get("source") == "upload":
                row = await settings_for_agent(db, target)
                ready = readiness(Path(target.config["config_dir"]), row)
                if not ready["ready"]:
                    raise AppError(
                        ErrorCode.VALIDATION, "전체 검사 준비 조건을 확인하세요.", {"criteria": ready["criteria"]}
                    )
        if body.mode == "agent" and not any(t.type == "agent" for t in targets):
            raise AppError(ErrorCode.VALIDATION, "에이전트 타깃을 선택하세요.")
        if (
            body.mode == "full"
            and any(t.config.get("source") == "upload" for t in targets)
            and not any(t.type == "agent" for t in targets)
        ):
            raise AppError(
                ErrorCode.VALIDATION,
                "전체 검사에는 에이전트 정의가 필요합니다. 소스 검사만 하려면 코드 모드를 선택하세요.",
            )
    uploaded_roots = {
        t.config.get("config_dir", t.ref) for t in targets if t.config.get("source") == "upload" and t.type == "agent"
    }
    if body.mode in {"full", "agent"} and len(uploaded_roots) > 1:
        raise AppError(ErrorCode.VALIDATION, "격리 실행 감사는 한 번에 업로드 프로젝트 하나를 선택하세요.")
    audit_id = uuid.uuid4()
    a = Audit(id=audit_id, requested_by=user.id, mode=body.mode, status="QUEUED", thread_id=f"audit:{audit_id}")
    db.add(a)
    await db.flush()
    db.add_all(AuditTarget(audit_id=audit_id, target_id=t.id) for t in targets)
    await db.commit()
    await publish(
        "audit.requested",
        str(audit_id),
        Envelope(
            type="audit.requested",
            trace_id=trace_id_for(str(audit_id)),
            payload={
                "audit_id": str(audit_id),
                "requested_by": str(user.id),
                "actor_role": user.role,
                "mode": body.mode,
                "target_ids": [str(t.id) for t in targets],
            },
        ),
    )
    return {"audit_id": str(audit_id), "status": "QUEUED"}


@router.get("/audits")
async def list_audits(
    user: CurrentUser = LoggedIn,
    p: Page = Depends(page_params),
    eval_only: bool = False,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    q = select(Audit)
    q = q.where(Audit.eval_config.is_not(None)) if eval_only else q.where(Audit.eval_config.is_(None))
    allowed = await accessible_target_ids(user, db)
    if allowed is not None:
        q = q.where(
            Audit.id.in_(select(AuditTarget.audit_id).where(AuditTarget.target_id.in_(allowed or {uuid.UUID(int=0)})))
        )
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = list((await db.execute(q.order_by(Audit.created_at.desc()).offset(p.offset).limit(p.size))).scalars())
    tmap = await _targets_of(db, [a.id for a in rows])
    r = get_redis()
    items = []
    for a in rows:
        live = await r.hgetall(f"audit:{a.id}:status") if a.status not in ("COMPLETED", "FAILED") else None
        items.append(audit_out(a, tmap.get(a.id, []), live))  # type: ignore[arg-type]
    return paged(items, total, p)


@router.get("/audits/{audit_id}")
async def get_audit(
    audit_id: uuid.UUID, user: CurrentUser = LoggedIn, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    a, tids = await load_audit(db, user, audit_id)
    live = await get_redis().hgetall(f"audit:{a.id}:status")
    out = audit_out(a, tids, live)  # type: ignore[arg-type]
    return out


@router.post("/audits/{audit_id}/cancel")
async def cancel_audit(
    audit_id: uuid.UUID, user: CurrentUser = Admin, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    a, tids = await load_audit(db, user, audit_id)
    if a.status in ("COMPLETED", "FAILED", "CANCELLED"):
        raise AppError(ErrorCode.CONFLICT, f"audit already {a.status}")
    a.status, a.finished_at = "CANCELLED", datetime.now(UTC)
    r = get_redis()
    for t in tids:
        if await r.get(f"lock:audit:target:{t}") == str(a.id):
            await r.delete(f"lock:audit:target:{t}")
    from core.sandbox_client import sandbox_call

    try:
        await sandbox_call("/stop/" + str(a.id), timeout=30)
    except AppError:
        import logging

        logging.getLogger(__name__).warning("sandbox cleanup deferred to TTL reaper: %s", a.id)
    return {"audit_id": str(a.id), "status": a.status}


# ---------------- SSE ----------------

TERMINAL_EVENTS = {"audit.completed", "audit.failed"}


async def _event_stream(request: Request, audit_id: str, last_id: str | None) -> AsyncIterator[dict[str, Any]]:
    r = get_redis()
    key = f"audit:{audit_id}:stream"
    cursor = last_id or "0-0"
    if not last_id and not await r.exists(key):
        # 스트림 만료 시 종료된 감사는 DB 영구 보관분을 재생한다. 첫 이벤트 전인 진행 중 감사는 아래 xread 로 대기.
        from core.db.session import session_scope

        async with session_scope() as db:
            audit = await db.get(Audit, uuid.UUID(audit_id))
            finished = audit is None or audit.status in ("COMPLETED", "FAILED", "CANCELLED")
            rows = []
            if finished:
                rows = list(
                    (
                        await db.execute(
                            select(AuditEvent).where(AuditEvent.audit_id == uuid.UUID(audit_id)).order_by(AuditEvent.ts)
                        )
                    ).scalars()
                )
        for e in rows:
            yield {
                "id": str(e.id),
                "event": e.type,
                "data": json.dumps(
                    {
                        "event_id": str(e.id),
                        "audit_id": audit_id,
                        "type": e.type,
                        "node": e.node,
                        "ts": e.ts.isoformat(),
                        "payload": {},
                    },
                    ensure_ascii=False,
                ),
            }
        if finished:
            return
    while not await request.is_disconnected():
        res: Any = await r.xread({key: cursor}, block=15000, count=100)
        if not res:
            yield {"event": "ping", "data": "{}"}
            continue
        for _stream, entries in res:
            for entry_id, fields in entries:
                cursor = entry_id
                data = fields.get("data", "{}")
                event = json.loads(data)
                event["payload"] = {}
                data = json.dumps(event, ensure_ascii=False)
                etype = event.get("type", "message")
                yield {"id": entry_id, "event": etype, "data": data}
                if etype in TERMINAL_EVENTS:
                    return
        await asyncio.sleep(0)


@router.get("/audits/{audit_id}/events")
async def audit_events(
    request: Request,
    audit_id: uuid.UUID,
    user: CurrentUser = LoggedIn,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    last_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> EventSourceResponse:
    """SSE. Last-Event-ID(=Redis Stream ID) 부터 재생. EventSource 는 헤더를 못 붙이므로 ?token= 허용."""
    await load_audit(db, user, audit_id)
    return EventSourceResponse(_event_stream(request, str(audit_id), last_event_id or last_id), ping=20)


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
