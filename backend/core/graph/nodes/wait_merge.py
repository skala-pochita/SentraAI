"""wait_merge: interrupt() 로 병합 대기. local 은 대시보드 Merge 버튼, github 는 webhook 이 재개시킨다."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from core.graph.deps import get_deps
from core.graph.nodes._base import node


@node("wait_merge")
async def wait_merge(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    proposals = state.get("fix_proposals", [])
    if not proposals:
        return {"merge_status": "SKIPPED"}
    await deps.event(
        state,
        "merge.waiting",
        {"proposals": [{"proposal_id": p["proposal_id"], "pr_url": p.get("pr_url")} for p in proposals]},
        node="wait_merge",
    )
    value = interrupt({"type": "merge", "proposal_ids": [p["proposal_id"] for p in proposals]})
    merged = bool(value.get("merged")) if isinstance(value, dict) else bool(value)
    return {"merge_status": "MERGED" if merged else "SKIPPED"}
