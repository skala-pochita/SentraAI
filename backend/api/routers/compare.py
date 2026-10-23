"""수정본 재업로드 비교: 같은 프로젝트의 이전 감사(기준)와 이번 감사의 Finding·동일 공격 결과 비교."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import CurrentUser, LoggedIn, accessible_target_ids, ensure_target_access
from api.routers.audits import _iso, _targets_of, check_baseline, load_audit, require_completed
from core.audit_compare import compare, render_md
from core.db.models import Audit, AuditTarget, Finding, Report, Target
from core.db.session import get_db
from core.errors import AppError, ErrorCode
from core.project_versions import project_id, project_ids, version

router = APIRouter(tags=["audits"])


async def _version_of(db: AsyncSession, target_ids: list[str]) -> int | None:
    for tid in target_ids:
        t = await db.get(Target, uuid.UUID(tid))
        if t is not None and t.type == "repo":
            return version(t)
    return None


async def _snapshot(db: AsyncSession, audit: Audit, target_ids: list[str]) -> dict[str, Any]:
    report = (await db.execute(select(Report).where(Report.audit_id == audit.id))).scalar_one_or_none()
    if report is None:
        raise AppError(ErrorCode.NOT_FOUND, "report not ready")
    rows = (await db.execute(select(Finding).where(Finding.audit_id == audit.id))).scalars()
    return {
        "audit_id": str(audit.id),
        "mode": audit.mode,
        "created_at": _iso(audit.created_at),
        "version": await _version_of(db, target_ids),
        "findings": [
            {
                "id": str(f.id),
                "source": f.source,
                "category": f.category,
                "rule_id": f.rule_id,
                "title": f.title,
                "severity": f.severity,
                "status": f.status,
                "evidence": f.evidence,
            }
            for f in rows
        ],
        "report": report.report_json,
    }


async def _comparison(
    db: AsyncSession, user: CurrentUser, audit_id: uuid.UUID, baseline: uuid.UUID | None
) -> dict[str, Any]:
    await require_completed(db, user, audit_id)
    audit, tids = await load_audit(db, user, audit_id)
    base_id = baseline or audit.baseline_audit_id
    if base_id is None:
        raise AppError(ErrorCode.VALIDATION, "비교할 기준 감사를 지정하세요 (baseline).")
    if base_id == audit_id:
        raise AppError(ErrorCode.VALIDATION, "같은 감사끼리는 비교할 수 없습니다.")
    await check_baseline(db, user, base_id, {uuid.UUID(t) for t in tids})
    base, base_tids = await load_audit(db, user, base_id)
    return compare(await _snapshot(db, base, base_tids), await _snapshot(db, audit, tids))


@router.get("/audits/{audit_id}/compare")
async def compare_audits(
    audit_id: uuid.UUID,
    baseline: uuid.UUID | None = None,
    user: CurrentUser = LoggedIn,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """기준(baseline, 생략 시 감사 생성 때 지정한 기준)과 비교. 판정: 신규·여전히 존재·해소/차단·확인 불가."""
    return await _comparison(db, user, audit_id, baseline)


@router.get("/audits/{audit_id}/compare.md", response_class=PlainTextResponse)
async def compare_markdown(
    audit_id: uuid.UUID,
    baseline: uuid.UUID | None = None,
    user: CurrentUser = LoggedIn,
    db: AsyncSession = Depends(get_db),
) -> PlainTextResponse:
    return PlainTextResponse(
        render_md(await _comparison(db, user, audit_id, baseline)),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="compare-{str(audit_id)[:8]}.md"'},
    )


@router.get("/targets/{tid}/baselines")
async def baseline_candidates(
    tid: uuid.UUID, user: CurrentUser = LoggedIn, db: AsyncSession = Depends(get_db)
) -> list[dict[str, Any]]:
    """이 대상과 같은 프로젝트(모든 버전)의 완료된 감사. 최신순 20건."""
    await ensure_target_access(user, db, {tid})
    family = await project_ids(db, {tid})
    targets = {t.id: t for t in (await db.execute(select(Target))).scalars()}

    def project_of(t: Target) -> str:
        repo = targets.get(t.linked_target_id) if t.type == "agent" and t.linked_target_id else None
        return project_id(repo or t)

    members = [t.id for t in targets.values() if project_of(t) in family]
    q = (
        select(Audit)
        .where(
            Audit.status == "COMPLETED",
            Audit.eval_config.is_(None),
            Audit.id.in_(select(AuditTarget.audit_id).where(AuditTarget.target_id.in_(members))),
        )
        .order_by(Audit.created_at.desc())
        .limit(20)
    )
    audits = list((await db.execute(q)).scalars())
    tmap = await _targets_of(db, [a.id for a in audits])
    allowed = await accessible_target_ids(user, db)
    out = []
    for a in audits:
        tids = tmap.get(a.id, [])
        if allowed is not None and not {uuid.UUID(t) for t in tids} <= allowed:
            continue
        out.append(
            {
                "id": str(a.id),
                "mode": a.mode,
                "created_at": _iso(a.created_at),
                "version": await _version_of(db, tids),
                "summary": a.summary,
            }
        )
    return out
