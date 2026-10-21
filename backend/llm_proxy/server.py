"""Restricted, non-streaming Chat Completions proxy with audit-bound quotas."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from api.errors import install_error_handlers
from core.db.models import Audit
from core.db.session import session_scope
from core.errors import AppError, ErrorCode
from core.proxy_sessions import record_success, reserve, session_key
from core.redis.client import get_redis
from core.secret_store import SecretStore
from core.settings import get_settings

app = FastAPI(title="Audit LLM Proxy")
install_error_handlers(app)


async def upstream_call(payload: dict[str, Any], provider_key: str) -> httpx.Response:
    async with httpx.AsyncClient(timeout=90, trust_env=False, follow_redirects=False) as client:
        return await client.post(
            get_settings().llm_proxy_upstream, json=payload, headers={"Authorization": "Bearer " + provider_key}
        )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


MAX_BODY = 128 * 1024
MESSAGE_FIELDS = {"role", "content", "name", "tool_call_id", "tool_calls", "function_call"}
FORWARDED_FIELDS = {
    "model",
    "messages",
    "tools",
    "tool_choice",
    "parallel_tool_calls",
    "temperature",
    "response_format",
    "reasoning_effort",
    "seed",
    "top_p",
    "stop",
}


def _bearer(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer ") or len(auth) > 256:
        raise AppError(ErrorCode.UNAUTHORIZED, "모델 호출 토큰 필요")
    return auth[7:]


async def _read_body(request: Request) -> tuple[bytes, Any]:
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_BODY:
            raise AppError(ErrorCode.VALIDATION, "모델 요청 크기 초과")
    try:
        return bytes(data), json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise AppError(ErrorCode.VALIDATION, "JSON 요청 형식 오류") from exc


def _validate_chat(body: Any) -> dict[str, Any]:
    """비스트리밍 텍스트 대화 + 함수 도구만 통과시킨다."""
    if (
        not isinstance(body, dict)
        or body.get("stream")
        or body.get("n", 1) != 1
        or not isinstance(body.get("messages"), list)
    ):
        raise AppError(ErrorCode.VALIDATION, "단일 비스트리밍 대화 요청만 지원합니다.")
    messages = body["messages"]
    if any(not isinstance(m, dict) or not isinstance(m.get("content"), (str, type(None))) for m in messages):
        raise AppError(ErrorCode.VALIDATION, "텍스트 대화만 지원합니다. 이미지·음성 입력은 허용하지 않습니다.")
    if any(set(m) - MESSAGE_FIELDS for m in messages):
        raise AppError(ErrorCode.VALIDATION, "지원하지 않는 메시지 필드입니다.")
    tools = body.get("tools")
    if "tools" in body and (
        not isinstance(tools, list) or any(not isinstance(t, dict) or t.get("type") != "function" for t in tools)
    ):
        raise AppError(ErrorCode.VALIDATION, "함수 도구만 지원합니다.")
    return body


async def _running_policy(token: str) -> dict[str, Any]:
    raw = await get_redis().get(session_key(token))
    if not raw:
        raise AppError(ErrorCode.UNAUTHORIZED, "모델 호출 토큰 만료")
    policy: dict[str, Any] = json.loads(raw)
    async with session_scope() as db:
        audit = await db.get(Audit, uuid.UUID(policy["audit_id"]))
        if audit is None or audit.status != "RUNNING":
            raise AppError(ErrorCode.FORBIDDEN, "실행 중인 감사의 요청만 허용합니다.")
    return policy


def _output_limit(body: dict[str, Any], policy: dict[str, Any]) -> int:
    requested = body.get("max_completion_tokens", body.get("max_tokens", policy["max_output_tokens"]))
    if not isinstance(requested, int) or isinstance(requested, bool) or requested < 1:
        raise AppError(ErrorCode.VALIDATION, "출력 토큰 한도 형식 오류")
    return min(requested, int(policy["max_output_tokens"]))


async def _forward(forwarded: dict[str, Any], key: str, token: str, reserved: int) -> JSONResponse:
    try:
        response = await upstream_call(forwarded, key)
        if response.status_code >= 400:
            # Provider errors may contain sensitive request details. Never return them verbatim.
            raise AppError(ErrorCode.UPSTREAM_ERROR, f"모델 제공자 요청 실패 (HTTP {response.status_code})")
        result: dict[str, Any] = response.json()
        if not isinstance(result, dict) or not isinstance(result.get("choices"), list):
            raise ValueError("unexpected response")
        tokens = result.get("usage", {}).get("total_tokens")
        await record_success(token, reserved, tokens if isinstance(tokens, int) and tokens >= 0 else None)
        return JSONResponse(result)
    except (httpx.HTTPError, ValueError) as exc:
        raise AppError(ErrorCode.UPSTREAM_ERROR, "모델 제공자 통신 실패") from exc


@app.post("/v1/chat/completions")
async def completion(request: Request) -> JSONResponse:
    token = _bearer(request)
    data, raw_body = await _read_body(request)
    body = _validate_chat(raw_body)
    policy = await _running_policy(token)
    output_limit = _output_limit(body, policy)
    # One UTF-8 byte per token is a conservative reservation for a bounded request.
    reserved = len(data) + output_limit + 512
    policy = await reserve(token, str(body.get("model", "")), reserved)
    key = await asyncio.to_thread(SecretStore().get, policy["target_id"] + ":provider", policy["provider_ref"])
    forwarded = {k: v for k, v in body.items() if k in FORWARDED_FIELDS}
    forwarded.update({"max_completion_tokens": output_limit, "stream": False, "store": False})
    return await _forward(forwarded, key, token, reserved)
