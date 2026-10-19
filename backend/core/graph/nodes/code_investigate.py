"""code_investigate: 정적 후보 검토·오탐 제거·놓친 취약점 발견.

LLM 이 설정돼 있으면 ReAct 조사(harness 모드, Phase 7), 없으면 정적 결과를 규칙 기준으로 확정한다.
"""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from core.graph.deps import get_deps
from core.graph.nodes._base import node
from core.graph.nodes.code_investigate_static import static_verdicts


@node("code_investigate")
async def code_investigate(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    candidates = state.get("candidates", [])
    llm = deps.llm("code_investigate", state.get("eval_config"))
    if llm is None:
        await deps.event(
            state, "investigate.skipped", {"reason": "LLM not configured; static verdicts"}, node="code_investigate"
        )
        return {"investigated": static_verdicts(candidates)}
    from core.graph.nodes.react_investigator import investigate

    return await investigate(state, config, llm)
