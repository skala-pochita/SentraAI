"""Cloud 파이프라인 테스트용 대역: 스크립트된 응답을 순서대로 돌려주는 가짜 채팅 모델."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage

from core.cloud.backend_mem import MemoryBackend
from core.cloud.budget import CloudBudget
from core.cloud.bundle import EvidenceBundle
from core.cloud.llm import StageRunner
from core.cloud.pipeline import CloudCtx
from core.cloud.tools import ToolHost
from tests.unit.test_cloud_checks import BASE


class RateLimitError(Exception):
    pass


class ScriptedModel:
    """responses: dict(JSON) 또는 Exception 또는 callable(messages)->dict. 소진되면 마지막 응답을 반복한다."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[list[Any]] = []

    async def ainvoke(self, messages: list[Any]) -> AIMessage:
        self.calls.append(messages)
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(r, Exception):
            raise r
        if callable(r):
            r = r(messages)
        msg = AIMessage(content=json.dumps(r, ensure_ascii=False))
        msg.usage_metadata = {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}  # type: ignore[typeddict-item]
        return msg


def make_ctx(model: Any | None, bundle: dict[str, Any] | None = None, **kw: Any) -> CloudCtx:
    from core.cloud.bundle import parse_bundle

    b: EvidenceBundle = parse_bundle(bundle or BASE)
    budget = CloudBudget.from_config()
    uris = ["guidance://security-baseline/v1"]
    events: list[dict[str, Any]] = []
    ctx = CloudCtx(
        task_id=kw.pop("task_id", "task-1"),
        bundle=b,
        host=None,  # type: ignore[arg-type]
        budget=budget,
        runner=StageRunner(model, "fake-model", budget) if model is not None else None,
        allowed_uris=uris,
        events=events,
        **kw,
    )
    ctx.host = ToolHost(
        MemoryBackend(b), budget, ctx.task_id, allowed_resource_uris=set(uris), on_event=lambda t, d: ctx.emit(t, d)
    )
    return ctx


def finding(**kw: Any) -> dict[str, Any]:
    base = {
        "area": "B1",
        "category": "TOOL_TO_SENSITIVE_RESOURCE_PATH",
        "resource_id": "bucket-customers",
        "status": "CONFIRMED",
        "severity": "HIGH",
        "evidence_ids": ["S-MCP", "POL-EXPORT", "bucket-customers"],
        "reason": "path",
        "path": ["T1", "R-EXPORT", "bucket-customers"],
    }
    base.update(kw)
    return base
