"""노드 공통 래퍼: node.started / node.finished(failed) 이벤트, H6 node span."""

from __future__ import annotations

import functools
import time
from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.runnables import RunnableConfig

from core.errors import AppError
from core.graph.deps import get_deps
from harness.observability import span

NodeFn = Callable[[dict[str, Any], RunnableConfig], Awaitable[dict[str, Any]]]


def node(name: str) -> Callable[[NodeFn], NodeFn]:
    def deco(fn: NodeFn) -> NodeFn:
        @functools.wraps(fn)
        async def wrapper(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
            deps = get_deps(config)
            started = time.monotonic()
            await deps.event(state, "node.started", {}, node=name)
            try:
                async with span("node", name):
                    out = await fn(state, config)
            except AppError as e:
                await deps.event(state, "node.failed", {"code": str(e.code), "message": e.message}, node=name)
                raise
            await deps.event(
                state, "node.finished", {"elapsed_ms": int((time.monotonic() - started) * 1000)}, node=name
            )
            return out

        return wrapper

    return deco


def target_of(state: dict[str, Any], type_: str) -> list[dict[str, Any]]:
    return [t for t in state.get("targets", []) if t.get("type") == type_]
