"""/eval-runs (명세 14장): 코드·Agent 감사 A/B/C 비교 실험."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import Admin, CurrentUser, LoggedIn
from core.db.models import EvalResult, EvalRun
from core.db.session import get_db
from core.errors import AppError, ErrorCode
from core.kafka.envelope import Envelope
from core.kafka.producer import publish
from core.settings import get_settings
from harness.eval.runner import CONFIGS, _agg, dataset_version

router = APIRouter(prefix="/eval-runs", tags=["eval"])


class EvalCreate(BaseModel):
    configs: list[str] = Field(default_factory=lambda: ["A_nano_single", "B_nano_harness", "C_large_single"])
    repeat: int = Field(default=1, ge=1, le=5)
    dataset_version: str | None = None


def run_out(r: EvalRun) -> dict[str, Any]:
    return {
        "id": str(r.id),
        "configs": r.configs,
        "dataset_version": r.dataset_version,
        "repeat": r.repeat,
        "status": r.status,
        "created_at": r.created_at.isoformat(),
    }


@router.post("", status_code=202)
async def create(body: EvalCreate, user: CurrentUser = Admin, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    bad = [c for c in body.configs if c not in CONFIGS]
    if bad:
        raise AppError(ErrorCode.VALIDATION, f"unknown configs {bad}")
    ver = dataset_version()
    if body.dataset_version and body.dataset_version != ver:
        raise AppError(ErrorCode.VALIDATION, f"dataset_version mismatch (current {ver})")
    run = EvalRun(requested_by=user.id, configs=body.configs, dataset_version=ver, repeat=body.repeat, status="QUEUED")
    db.add(run)
    await db.flush()
    await db.commit()
    await publish(
        "eval.requested",
        str(run.id),
        Envelope(
            type="eval.requested", payload={"eval_run_id": str(run.id), "configs": body.configs, "repeat": body.repeat}
        ),
    )
    return run_out(run)


@router.get("")
async def list_runs(_: CurrentUser = LoggedIn, db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    rows = (await db.execute(select(EvalRun).order_by(EvalRun.created_at.desc()).limit(50))).scalars()
    return [run_out(r) for r in rows]


@router.get("/{run_id}")
async def get_run(run_id: uuid.UUID, _: CurrentUser = LoggedIn, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    r = await db.get(EvalRun, run_id)
    if r is None:
        raise AppError(ErrorCode.NOT_FOUND, "eval run not found")
    results = list(
        (
            await db.execute(
                select(EvalResult)
                .where(EvalResult.eval_run_id == run_id)
                .order_by(EvalResult.config, EvalResult.repeat_idx)
            )
        ).scalars()
    )
    by_cfg: dict[str, list[dict[str, Any]]] = {}
    for x in results:
        by_cfg.setdefault(x.config, []).append({"metrics": x.metrics, "per_case": x.per_case})
    report = get_settings().reports_dir / f"eval_{run_id}.md"
    return {
        **run_out(r),
        "results": [
            {
                "config": x.config,
                "repeat_idx": x.repeat_idx,
                "audit_id": str(x.audit_id) if x.audit_id else None,
                "metrics": x.metrics,
                "per_case": x.per_case,
            }
            for x in results
        ],
        "summary": {c: _agg(v) for c, v in by_cfg.items()},
        "report_md": report.read_text(encoding="utf-8") if report.is_file() else None,
    }
