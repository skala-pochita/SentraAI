"""apply_fix: 승인 ID 를 첨부해 브랜치·PR 생성 (멱등 키 = audit:proposal), 대상 Finding 상태 FIXING."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from core.graph.deps import get_deps
from core.graph.nodes._base import node


@node("apply_fix")
async def apply_fix(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    approval = state.get("approval") or {}
    approval_id = approval.get("approval_id") if approval.get("decision") == "APPROVE" else None
    out = []
    for p in state.get("fix_proposals", []):
        res = await deps.call(
            state,
            "apply_fix",
            "apply_fix",
            proposal_id=p["proposal_id"],
            idempotency_key=f"{state['audit_id']}:{p['proposal_id']}",
            approval_id=approval_id,
        )
        out.append({**p, "branch": res["branch"], "pr_url": res["pr_url"]})
        await deps.event(
            state,
            "fix.applied",
            {
                "proposal_id": p["proposal_id"],
                "branch": res["branch"],
                "pr_url": res["pr_url"],
                "reused": res.get("reused"),
            },
            node="apply_fix",
        )
    for fid in state.get("fix_targets", []):
        await deps.call(
            state, "apply_fix", "update_finding_status", finding_id=fid, status="FIXING", reason="fix proposal applied"
        )
    return {"fix_proposals": out, "merge_status": "PENDING"}
