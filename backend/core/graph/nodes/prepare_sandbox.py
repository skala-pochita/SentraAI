"""Launch uploaded agents; unexecuted attacks retain explicit coverage status."""

from typing import Any

from langchain_core.runnables import RunnableConfig

from core.errors import AppError
from core.graph.deps import get_deps, node_error
from core.graph.nodes._base import node


@node("prepare_sandbox")
async def prepare_sandbox(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    try:
        result = await deps.call(
            state, "prepare_sandbox", "prepare_agent_sandbox", target_id=state["target"]["target_id"]
        )
        return {"runtime": result}
    except AppError as exc:
        return {"runtime": {"status": "ERROR", "reason": exc.message}, "errors": [node_error("prepare_sandbox", exc)]}
