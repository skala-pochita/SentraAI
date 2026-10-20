"""그래프 실행 의존성 (config["configurable"]["deps"] 로 전달; 체크포인트에는 저장되지 않는다)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig

from core.errors import AppError, ErrorCode
from core.mcp_client import CallCtx, Gateway
from harness.budget import Budget
from harness.events import emit
from harness.llm import llm_available, make_chat_model, model_name_for

LLMFactory = Callable[[str, str | None], tuple[BaseChatModel, str] | None]


def default_llm_factory(node: str, eval_config: str | None) -> tuple[BaseChatModel, str] | None:
    """(모델, 모델명) 또는 LLM 미설정 시 None (노드는 정적 결과로 진행)."""
    if not llm_available():
        return None
    name = model_name_for(node, eval_config)
    return make_chat_model(name), name


@dataclass
class Deps:
    gateway: Gateway
    budget: Budget = field(default_factory=Budget.from_config)
    llm: LLMFactory = default_llm_factory
    emit_events: bool = True

    async def call(self, state: dict[str, Any], node: str, tool: str, **args: Any) -> Any:
        ctx = CallCtx(
            node=node,
            actor_role=state.get("actor_role", "ADMIN"),
            actor_id=state.get("requested_by"),
            audit_id=state.get("audit_id"),
        )
        return await self.gateway.call(tool, args, ctx)

    async def event(
        self,
        state: dict[str, Any],
        type_: str,
        payload: dict[str, Any] | None = None,
        node: str | None = None,
        progress: float | None = None,
    ) -> None:
        if self.emit_events and state.get("audit_id"):
            await emit(state["audit_id"], type_, payload, node=node, progress=progress)


def get_deps(config: RunnableConfig) -> Deps:
    deps = (config.get("configurable") or {}).get("deps")
    if not isinstance(deps, Deps):
        raise AppError(ErrorCode.INTERNAL, "graph deps not configured")
    return deps


def node_error(node: str, err: AppError) -> dict[str, Any]:
    out = {"node": node, "code": str(err.code), "message": err.message, "retryable": err.retryable}
    if err.details:
        out["details"] = err.details
    return out
