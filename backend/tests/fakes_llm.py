"""테스트용 LLM 대역: 스크립트된 응답을 순서대로 돌려주는 가짜 채팅 모델."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage


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
