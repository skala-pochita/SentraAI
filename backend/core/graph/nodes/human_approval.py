"""human_approval: CRITICAL 목록으로 request_approval 호출 후 interrupt(). 재개 값으로 decision 받음.

interrupt 후 재개되면 노드가 처음부터 다시 실행되므로 request_approval 은 멱등이어야 한다(같은 Finding 집합 → 같은 승인).
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.types import interrupt

from core.graph.deps import get_deps
from core.graph.nodes._base import node


def critical_ids(state: dict[str, Any]) -> list[str]:
    return [f["finding_id"] for f in state.get("correlated", []) if f["severity"] == "CRITICAL" and f.get("finding_id")]


@node("human_approval")
async def human_approval(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    ids = critical_ids(state)
    titles = [f["title"] for f in state.get("correlated", []) if f.get("finding_id") in ids]
    summary = f"CRITICAL {len(ids)}건 수정 적용 승인 요청: " + "; ".join(titles[:5])
    res = await deps.call(
        state, "human_approval", "request_approval", audit_id=state["audit_id"], finding_ids=ids, summary=summary
    )
    await deps.event(
        state,
        "approval.requested",
        {"approval_id": res["approval_id"], "count": len(ids), "titles": titles[:5]},
        node="human_approval",
    )
    decision = interrupt(
        {"type": "approval", "approval_id": res["approval_id"], "finding_ids": ids, "summary": summary}
    )
    approval = {
        "approval_id": res["approval_id"],
        **(decision if isinstance(decision, dict) else {"decision": decision}),
    }
    await deps.event(
        state,
        "approval.decided",
        {"approval_id": res["approval_id"], "decision": approval.get("decision")},
        node="human_approval",
    )
    return {"approval": approval}
