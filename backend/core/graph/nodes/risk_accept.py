"""risk_accept: 반려된 CRITICAL 을 위험 수용으로 등록 (사유, 만료일)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.runnables import RunnableConfig

from core.graph.deps import get_deps
from core.graph.nodes._base import node
from core.graph.nodes.human_approval import critical_ids

ACCEPT_DAYS = 90


@node("risk_accept")
async def risk_accept(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    approval = state.get("approval") or {}
    reason = approval.get("comment") or "보안관리자 반려: 위험 수용"
    expires = (datetime.now(UTC) + timedelta(days=ACCEPT_DAYS)).isoformat()
    accepted = []
    for fid in critical_ids(state):
        await deps.call(
            state, "risk_accept", "register_risk_acceptance", finding_id=fid, reason=reason, expires_at=expires
        )
        accepted.append(fid)
    await deps.event(state, "risk.accepted", {"count": len(accepted), "expires_at": expires}, node="risk_accept")
    return {"accepted_risks": accepted}
