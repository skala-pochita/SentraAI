"""Kafka 메시지 envelope (명세 11.2)."""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

TOPICS = (
    "audit.requested",
    "audit.resume",
    "audit.completed",
    "github.webhook",
    "eval.requested",
    "eval.tasks",
    "jobs.retry",
    "jobs.dlq",
)
MAX_RETRIES = 3
RETRY_DELAYS_S = (5, 30, 120)


class Envelope(BaseModel):
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: str
    version: int = 1
    occurred_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    trace_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    payload: dict[str, Any]

    def encode(self) -> bytes:
        return self.model_dump_json().encode()

    @classmethod
    def decode(cls, raw: bytes) -> Envelope:
        return cls.model_validate(json.loads(raw))


# 헤더: retry_count, original_topic, next_attempt_at(epoch ms), last_error_code, last_error_message
Headers = dict[str, str]


def headers_to_kafka(h: Headers) -> list[tuple[str, bytes]]:
    return [(k, str(v).encode()) for k, v in h.items()]


def headers_from_kafka(h: list[tuple[str, bytes]] | tuple[tuple[str, bytes], ...] | None) -> Headers:
    return {k: v.decode() for k, v in (h or [])}


def retry_headers(prev: Headers, original_topic: str, code: str, message: str) -> tuple[Headers, bool]:
    """(새 헤더, DLQ 로 보낼지). retry_count < 3 이면 jobs.retry (5s/30s/120s 지수 백오프)."""
    count = int(prev.get("retry_count", "0"))
    out = {
        "retry_count": str(count + 1),
        "original_topic": prev.get("original_topic", original_topic),
        "last_error_code": code,
        "last_error_message": message[:500],
    }
    if count >= MAX_RETRIES:
        return {**out, "retry_count": str(count)}, True
    out["next_attempt_at"] = str(int(time.time() * 1000) + RETRY_DELAYS_S[count] * 1000)
    return out, False
