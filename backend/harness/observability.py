"""H6 관측성: 노드·LLM·도구 호출 단위 트레이스 (trace_spans), 토큰·비용·지연.

LANGSMITH_API_KEY 가 있으면 LangChain 표준 환경변수로 LangSmith 트레이싱을 켠다.
"""

from __future__ import annotations

import contextvars
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from core.db.models import TraceSpan
from core.db.session import session_scope
from core.errors import AppError
from core.settings import get_settings, load_config

log = logging.getLogger("trace")

_parent: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_parent", default=None)
_audit: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_audit", default=None)
_eval_run: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_eval_run", default=None)


def configure_langsmith() -> None:
    key = get_settings().langsmith_api_key
    if key:
        os.environ.setdefault("LANGSMITH_TRACING", "true")
        os.environ.setdefault("LANGSMITH_API_KEY", key)
        os.environ.setdefault("LANGSMITH_PROJECT", "ai-security-auditor")


def set_trace_context(audit_id: str | None, eval_run_id: str | None = None) -> None:
    _audit.set(audit_id)
    _eval_run.set(eval_run_id)


def cost_usd(model: str | None, input_tokens: int, output_tokens: int) -> float:
    if not model:
        return 0.0
    prices = load_config("pricing").get("models", {}).get(model)
    if not prices:
        return 0.0
    return (input_tokens * float(prices.get("input", 0)) + output_tokens * float(prices.get("output", 0))) / 1e6


@dataclass
class Span:
    id: str
    kind: str
    name: str
    started: float
    started_at: datetime
    model: str | None = None
    prompt_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    status: str = "ok"
    error_code: str | None = None
    attrs: dict[str, Any] = field(default_factory=dict)

    def usage(self, model: str | None, input_tokens: int, output_tokens: int) -> None:
        self.model = model or self.model
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens


async def _persist(span: Span, parent: str | None) -> None:
    audit_id, eval_run = _audit.get(), _eval_run.get()
    if not audit_id and not eval_run:
        return
    try:
        async with session_scope() as db:
            db.add(
                TraceSpan(
                    id=uuid.UUID(span.id),
                    audit_id=uuid.UUID(audit_id) if audit_id else None,
                    eval_run_id=uuid.UUID(eval_run) if eval_run else None,
                    parent_id=uuid.UUID(parent) if parent else None,
                    kind=span.kind,
                    name=span.name,
                    model=span.model,
                    prompt_id=span.prompt_id,
                    input_tokens=span.input_tokens or None,
                    output_tokens=span.output_tokens or None,
                    cost_usd=Decimal(str(round(cost_usd(span.model, span.input_tokens, span.output_tokens), 6))),
                    latency_ms=int((time.monotonic() - span.started) * 1000),
                    status=span.status,
                    error_code=span.error_code,
                    started_at=span.started_at,
                )
            )
    except (SQLAlchemyError, OSError, ValueError) as e:
        log.warning("span persist failed %s/%s: %s", span.kind, span.name, type(e).__name__)


@asynccontextmanager
async def span(kind: str, name: str, **attrs: Any) -> AsyncIterator[Span]:
    s = Span(
        str(uuid.uuid4()),
        kind,
        name,
        time.monotonic(),
        datetime.now(UTC),
        attrs=attrs,
        model=attrs.get("model"),
        prompt_id=attrs.get("prompt_id"),
    )
    parent = _parent.get()
    token = _parent.set(s.id)
    try:
        yield s
    except AppError as e:
        s.status, s.error_code = "error", str(e.code)
        raise
    except BaseException as e:
        s.status, s.error_code = "error", type(e).__name__
        raise
    finally:
        _parent.reset(token)
        await _persist(s, parent)
