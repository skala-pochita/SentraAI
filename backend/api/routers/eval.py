"""/eval: Cloud 평가 결과(고정 JSON) 조회. 실행은 `make eval` (CLI). 화면은 이 JSON 을 읽는 정적 표·차트다 (기획서 12장)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter

from api.deps import CurrentUser, LoggedIn
from core.errors import AppError, ErrorCode
from core.settings import get_settings

router = APIRouter(prefix="/eval", tags=["eval"])


def _files() -> list[Path]:
    d = get_settings().reports_dir
    return sorted(d.glob("cloud_eval_*.json"), reverse=True) if d.is_dir() else []


@router.get("/cloud-runs")
async def list_runs(_: CurrentUser = LoggedIn) -> list[dict[str, Any]]:
    out = []
    for p in _files():
        ev = json.loads(p.read_text(encoding="utf-8"))
        out.append(
            {
                "eval_id": ev["eval_id"],
                "configs": ev["configs"],
                "bundles": len(ev["bundles"]),
                "repetitions": ev["repetitions"],
                "models": ev["models"],
                "executed": {c: ev["summary"][c]["executed"] for c in ev["configs"]},
                "skipped": {c: ev["summary"][c]["skipped"] for c in ev["configs"]},
            }
        )
    return out


@router.get("/cloud-runs/{eval_id}")
async def get_run(eval_id: str, _: CurrentUser = LoggedIn) -> dict[str, Any]:
    if not eval_id.replace("T", "").isdigit():  # 경로 탈출 방지: 숫자·T 만 허용
        raise AppError(ErrorCode.VALIDATION, "invalid eval_id")
    path = get_settings().reports_dir / f"cloud_eval_{eval_id}.json"
    if not path.is_file():
        raise AppError(ErrorCode.NOT_FOUND, "eval run not found")
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data
