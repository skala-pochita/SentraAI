"""mark_manual: 재검증 3회 후에도 미해결인 Finding → MANUAL_REQUIRED."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from core.errors import AppError
from core.graph.deps import get_deps, node_error
from core.graph.nodes._base import node


@node("mark_manual")
async def mark_manual(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    ids = (state.get("verify_result") or {}).get("unresolved", [])
    errors = []
    for fid in ids:
        try:
            await deps.call(
                state,
                "mark_manual",
                "update_finding_status",
                finding_id=fid,
                status="MANUAL_REQUIRED",
                reason="automated fix did not resolve after retries",
            )
        except AppError as e:  # 아직 FIXING 이 아닌 건(수정안 없음)은 전이 불가 → 기록만
            errors.append(node_error("mark_manual", e))
    await deps.event(state, "manual.required", {"count": len(ids)}, node="mark_manual")
    return {"errors": errors}
