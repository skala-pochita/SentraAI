"""Opaque audit-bound LLM grants. Atomic reservation limits concurrent usage."""

from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any

from redis.exceptions import WatchError

from core.errors import AppError, ErrorCode
from core.execution_config import ExecutionConfig
from core.redis.client import get_redis

TTL = 900


def session_key(token: str) -> str:
    return "llm:grant:" + hashlib.sha256(token.encode()).hexdigest()


async def issue(audit_id: str, target_id: str, config: ExecutionConfig, provider_ref: str) -> str:
    token = secrets.token_urlsafe(32)
    key = session_key(token)
    redis = get_redis()
    previous = await usage(audit_id)
    existing = await redis.get("llm:audit:" + audit_id)
    if existing:
        await redis.delete(existing)
    await redis.set(
        key,
        json.dumps(
            {
                "audit_id": audit_id,
                "target_id": target_id,
                "provider_ref": provider_ref,
                "model": config.model,
                "max_requests": config.max_requests,
                "max_total_tokens": config.max_total_tokens,
                "max_output_tokens": config.max_output_tokens,
                "requests": previous.get("requests", 0),
                "successful_requests": previous.get("successful_requests", 0),
                "charged_tokens": previous.get("charged_tokens", 0),
            }
        ),
        ex=TTL,
    )
    await redis.set("llm:audit:" + audit_id, key, ex=TTL)
    return token


async def reserve(token: str, model: str, estimated_tokens: int) -> dict[str, Any]:
    redis = get_redis()
    key = session_key(token)
    for _ in range(10):
        async with redis.pipeline(transaction=True) as pipe:
            try:
                await pipe.watch(key)
                raw = await pipe.get(key)
                if not raw:
                    raise AppError(ErrorCode.UNAUTHORIZED, "모델 호출 토큰이 만료되었거나 취소되었습니다.")
                state = json.loads(raw)
                if model != state["model"]:
                    raise AppError(ErrorCode.FORBIDDEN, "허용되지 않은 모델입니다.")
                if (
                    state["requests"] >= state["max_requests"]
                    or state["charged_tokens"] + estimated_tokens > state["max_total_tokens"]
                ):
                    raise AppError(ErrorCode.BUDGET_EXCEEDED, "대상 모델 호출 한도에 도달했습니다.")
                state["requests"] += 1
                state["charged_tokens"] += estimated_tokens
                ttl = await pipe.ttl(key)
                if ttl <= 0:
                    raise AppError(ErrorCode.UNAUTHORIZED, "모델 호출 토큰 만료")
                pipe.multi()
                pipe.set(key, json.dumps(state), ex=ttl)
                await pipe.execute()
                return state
            except WatchError:
                continue
    raise AppError(ErrorCode.CONFLICT, "동시 모델 요청이 많습니다.")


async def record_success(token: str, reserved: int, actual: int | None) -> None:
    redis = get_redis()
    key = session_key(token)
    for _ in range(10):
        async with redis.pipeline(transaction=True) as pipe:
            try:
                await pipe.watch(key)
                raw = await pipe.get(key)
                if not raw:
                    return
                state = json.loads(raw)
                state["successful_requests"] += 1
                if actual is not None:
                    state["charged_tokens"] = max(0, state["charged_tokens"] - reserved + actual)
                ttl = await pipe.ttl(key)
                if ttl <= 0:
                    return
                pipe.multi()
                pipe.set(key, json.dumps(state), ex=ttl)
                await pipe.execute()
                return
            except WatchError:
                continue
    raise AppError(ErrorCode.CONFLICT, "모델 사용량 기록 충돌")


async def usage(audit_id: str) -> dict[str, Any]:
    redis = get_redis()
    key = await redis.get("llm:audit:" + audit_id)
    raw = await redis.get(key) if key else None
    if not raw:
        raw = await redis.get("llm:usage:" + audit_id)
    state = json.loads(raw) if raw else {}
    return {
        k: state.get(k, 0 if k != "model" else None)
        for k in ("requests", "successful_requests", "charged_tokens", "model")
    }


async def revoke(audit_id: str) -> None:
    redis = get_redis()
    key = await redis.get("llm:audit:" + audit_id)
    if key:
        state = await usage(audit_id)
        await redis.set("llm:usage:" + audit_id, json.dumps(state), ex=86400 * 7)
        await redis.delete(key)
    await redis.delete("llm:audit:" + audit_id)
