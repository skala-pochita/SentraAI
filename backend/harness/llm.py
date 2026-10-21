"""LLM 호출 헬퍼: 노드별 모델(config/models.yaml), 구조화 출력 + H4 스키마 검증 재요청(최대 2회),
H6 llm span, H8 예산 차감, 토큰 사용량 기록."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

from core.errors import AppError, ErrorCode
from core.settings import get_settings, load_config
from harness.budget import Budget
from harness.observability import cost_usd, span
from harness.prompts import PromptSpec

log = logging.getLogger("llm")
M = TypeVar("M", bound=BaseModel)

MAX_SCHEMA_RETRIES = 2


def model_name_for(node: str, eval_config: str | None = None) -> str:
    cfg = load_config("models")
    if eval_config and eval_config in cfg.get("eval_configs", {}):
        name = str(cfg["eval_configs"][eval_config].get("model") or "")
        if name:
            return name
    return str(cfg.get("nodes", {}).get(node, {}).get("model") or cfg["defaults"]["model"])


def llm_available() -> bool:
    return bool(get_settings().openai_api_key)


def make_chat_model(model: str) -> BaseChatModel:
    from langchain_openai import ChatOpenAI

    cfg = load_config("models")["defaults"]
    kwargs: dict[str, Any] = {"model": model, "timeout": float(cfg.get("timeout_s", 60)), "max_retries": 2}
    temp = cfg.get("temperature")
    if temp is not None and not model.startswith(("gpt-5", "o")):  # 일부 모델은 temperature 고정
        kwargs["temperature"] = float(temp)
    return ChatOpenAI(**kwargs)


def usage_of(msg: AIMessage) -> tuple[int, int]:
    um = getattr(msg, "usage_metadata", None) or {}
    return int(um.get("input_tokens", 0) or 0), int(um.get("output_tokens", 0) or 0)


def _extract_json(text: str) -> Any:
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    blob = m.group(1) if m else text
    start = min([i for i in (blob.find("{"), blob.find("[")) if i >= 0], default=-1)
    if start < 0:
        raise ValueError("no JSON object in output")
    return json.loads(blob[start:])


async def invoke_messages(
    model: BaseChatModel,
    messages: list[BaseMessage],
    *,
    node: str,
    model_name: str,
    prompt_ref: str | None,
    budget: Budget | None,
    usage: list[dict[str, Any]],
) -> AIMessage:
    if budget is not None and budget.exceeded():
        raise AppError(ErrorCode.BUDGET_EXCEEDED, "audit token/cost budget exceeded", budget.snapshot())
    async with span("llm", node, model=model_name, prompt_id=prompt_ref) as s:
        try:
            msg = await model.ainvoke(messages)
        except AppError:
            raise
        except Exception as e:  # noqa: BLE001 - SDK 예외를 구조화된 오류로 변환
            name = type(e).__name__
            code = (
                ErrorCode.UNAUTHORIZED
                if "Authentication" in name or "PermissionDenied" in name
                else ErrorCode.RATE_LIMITED
                if "RateLimit" in name
                else ErrorCode.UPSTREAM_TIMEOUT
                if "Timeout" in name
                else ErrorCode.UPSTREAM_ERROR
            )
            raise AppError(code, f"LLM call failed: {name}") from e
        if not isinstance(msg, AIMessage):
            msg = AIMessage(content=str(getattr(msg, "content", msg)))
        tin, tout = usage_of(msg)
        s.usage(model_name, tin, tout)
    usd = cost_usd(model_name, tin, tout)
    if budget is not None:
        budget.charge(tin + tout, usd)
    usage.append(
        {"node": node, "model": model_name, "in": tin, "out": tout, "usd": round(usd, 6), "prompt": prompt_ref}
    )
    return msg


async def structured_call(
    model: BaseChatModel,
    prompt: PromptSpec,
    schema: type[M],
    *,
    node: str,
    model_name: str,
    budget: Budget | None,
    usage: list[dict[str, Any]],
    **vars: Any,
) -> M:
    """JSON 출력을 schema 로 검증. 실패하면 오류 메시지를 포함해 최대 2회 재요청 (H4 출력 가드레일)."""
    messages: list[BaseMessage] = [
        SystemMessage(
            prompt.render_system(**vars)
            + "\n\n반드시 JSON 하나만 출력한다. 스키마:\n"
            + json.dumps(schema.model_json_schema(), ensure_ascii=False)
        ),
        HumanMessage(prompt.render(**vars)),
    ]
    last_err = ""
    for attempt in range(MAX_SCHEMA_RETRIES + 1):
        msg = await invoke_messages(
            model, messages, node=node, model_name=model_name, prompt_ref=prompt.ref, budget=budget, usage=usage
        )
        text = msg.content if isinstance(msg.content, str) else json.dumps(msg.content, ensure_ascii=False)
        try:
            return schema.model_validate(_extract_json(text))
        except (ValueError, ValidationError) as e:
            last_err = str(e)[:500]
            log.info("schema validation failed node=%s attempt=%d", node, attempt)
            messages += [
                msg,
                HumanMessage(f"출력이 스키마 검증에 실패했다: {last_err}\n스키마에 맞는 JSON 만 다시 출력하라."),
            ]
    raise AppError(ErrorCode.VALIDATION, f"LLM output failed schema validation: {last_err}", {"node": node})
