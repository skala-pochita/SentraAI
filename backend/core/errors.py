"""구조화된 오류 (명세 9.2 오류 코드 표). API·MCP·워커 공통."""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    VALIDATION = "VALIDATION"
    NOT_FOUND = "NOT_FOUND"
    FORBIDDEN = "FORBIDDEN"
    UNAUTHORIZED = "UNAUTHORIZED"
    CONFLICT = "CONFLICT"
    RATE_LIMITED = "RATE_LIMITED"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    UPSTREAM_ERROR = "UPSTREAM_ERROR"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    INTERNAL = "INTERNAL"


RETRYABLE = {ErrorCode.RATE_LIMITED, ErrorCode.UPSTREAM_TIMEOUT, ErrorCode.UPSTREAM_ERROR}

HTTP_STATUS = {
    ErrorCode.VALIDATION: 422,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.CONFLICT: 409,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.UPSTREAM_TIMEOUT: 504,
    ErrorCode.UPSTREAM_ERROR: 502,
    ErrorCode.BUDGET_EXCEEDED: 429,
    ErrorCode.INTERNAL: 500,
}


class AppError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
        retry_after_s: float | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.details = details or {}
        self.retry_after_s = retry_after_s

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "code": str(self.code),
            "message": self.message,
            "retryable": self.retryable,
            "details": self.details,
        }
        if self.retry_after_s is not None:
            out["retry_after_s"] = self.retry_after_s
        return out


class RetryableNodeError(AppError):
    """노드 안 짧은 재시도 후에도 실패 → Kafka 재시도(11장)로 넘김."""
