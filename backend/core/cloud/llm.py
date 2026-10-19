"""Cloud 감사용 LLM 호출: 턴·시간 예산, 429/503 재시도(최대 2회, SDK 자동 재시도는 끈다), 사용량 집계."""

from __future__ import annotations

import asyncio
from typing import Any, TypeVar

from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from core.cloud.budget import CloudBudget
from core.errors import AppError, ErrorCode
from core.settings import load_config
from harness.llm import structured_call
from harness.prompts import PromptSpec, get_prompt

M = TypeVar("M", bound=BaseModel)
_TRANSIENT = ("RateLimit", "Timeout", "ServiceUnavailable", "InternalServer", "APIConnection", "Overloaded")


def _retry_after(e: Exception) -> float | None:
    headers = getattr(getattr(e, "response", None), "headers", None)
    try:
        return float(headers["retry-after"]) if headers and "retry-after" in headers else None
    except (TypeError, ValueError):
        return None


class CountingModel:
    """ainvoke 마다 provider_request_count 를 올리고, 일시 오류는 예산 안에서만 재시도한다."""

    def __init__(self, inner: Any, budget: CloudBudget) -> None:
        self.inner, self.budget = inner, budget
        self.backoff = [
            float(x) for x in load_config("budget").get("cloud_audit", {}).get("retry_backoff_s", [1.0, 2.0])
        ]

    async def ainvoke(self, messages: list[BaseMessage]) -> Any:
        attempt = 0
        while True:
            self.budget.provider_request_count += 1
            try:
                return await self.inner.ainvoke(messages)
            except Exception as e:  # noqa: BLE001 - 일시 오류만 재시도하고 나머지는 그대로 전달
                transient = any(t in type(e).__name__ for t in _TRANSIENT)
                if not transient or attempt >= self.budget.provider_retries:
                    raise
                delay = _retry_after(e)
                if delay is None:
                    delay = self.backoff[min(attempt, len(self.backoff) - 1)]
                if delay >= self.budget.remaining_s:  # 남은 활성 시간을 넘기는 대기는 하지 않는다
                    raise
                attempt += 1
                await asyncio.sleep(delay)


class StageRunner:
    def __init__(self, model: Any, model_name: str, budget: CloudBudget) -> None:
        self.model = CountingModel(model, budget)
        self.model_name, self.budget = model_name, budget
        self.usage: list[dict[str, Any]] = []

    async def run(self, prompt_id: str, schema: type[M], node: str, **vars: Any) -> M:
        spec: PromptSpec = get_prompt(prompt_id)
        self.budget.start_turn(node)
        before = len(self.usage)
        try:
            return await structured_call(
                self.model,  # type: ignore[arg-type]
                spec,
                schema,
                node=node,
                model_name=self.model_name,
                budget=None,
                usage=self.usage,
                **vars,
            )
        except AppError as e:
            if e.code == ErrorCode.VALIDATION:
                raise AppError(ErrorCode.VALIDATION, f"{node}: model output invalid", {"node": node}) from e
            raise
        finally:
            for u in self.usage[before:]:
                self.budget.charge(int(u["in"]) + int(u["out"]), float(u["usd"]))
