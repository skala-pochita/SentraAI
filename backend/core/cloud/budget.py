"""Agent·Cloud 감사 실행 예산 (기획서 6장 Agent Loop). A/B 공통.

LLM 턴 8, 활성 시간 120초(승인 대기 제외, 재개해도 초기화하지 않는다), 같은 도구 3회, 429/503 재시도 2회.
한도 근처에서는 새 호출을 하지 않고 호출자가 판단 보류로 마무리하도록 `can_*` 로 먼저 묻는다.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from core.errors import AppError, ErrorCode
from core.settings import load_config


@dataclass
class CloudBudget:
    max_llm_turns: int = 8
    max_active_s: float = 120.0
    min_remaining_for_collect_s: float = 30.0
    max_same_tool_calls: int = 3
    provider_retries: int = 2
    max_cost_usd: float = 0.5
    llm_turns: int = 0
    provider_request_count: int = 0
    active_elapsed_ms: int = 0
    repair_count: int = 0
    max_repairs: int = 1
    usd: float = 0.0
    tokens: int = 0
    tool_calls: Counter[str] = field(default_factory=Counter)
    _started: float | None = field(default=None, repr=False)

    @classmethod
    def from_config(cls) -> CloudBudget:
        c = load_config("budget").get("cloud_audit", {})
        return cls(
            max_llm_turns=int(c.get("max_llm_turns", 8)),
            max_active_s=float(c.get("max_active_seconds", 120)),
            min_remaining_for_collect_s=float(c.get("min_remaining_for_collect_s", 30)),
            max_same_tool_calls=int(c.get("max_same_tool_calls", 3)),
            provider_retries=int(c.get("provider_retries", 2)),
            max_cost_usd=float(c.get("max_cost_usd", 0.5)),
            max_repairs=int(c.get("repair_rounds", 1)),
        )

    # ---- 활성 시간: start/pause 로 승인 대기 구간을 제외한다 ----
    def resume_clock(self) -> None:
        if self._started is None:
            self._started = time.monotonic()

    def pause_clock(self) -> None:
        if self._started is not None:
            self.active_elapsed_ms += int((time.monotonic() - self._started) * 1000)
            self._started = None

    @property
    def active_s(self) -> float:
        running = (time.monotonic() - self._started) if self._started is not None else 0.0
        return self.active_elapsed_ms / 1000 + running

    @property
    def remaining_s(self) -> float:
        return max(0.0, self.max_active_s - self.active_s)

    # ---- 가드 ----
    def can_call_llm(self) -> bool:
        return self.llm_turns < self.max_llm_turns and self.remaining_s > 0 and self.usd < self.max_cost_usd

    def can_collect_more(self) -> bool:
        """추가 collect/repair 가능 여부: 턴 여유(보고서 1턴 예약) + 남은 시간 + 비용."""
        return (
            self.llm_turns < self.max_llm_turns - 1
            and self.remaining_s > self.min_remaining_for_collect_s
            and self.usd < self.max_cost_usd
        )

    def can_repair(self) -> bool:
        return (
            self.repair_count < self.max_repairs
            and self.llm_turns <= self.max_llm_turns - 3
            and self.can_collect_more()
        )

    def start_turn(self, node: str) -> None:
        if not self.can_call_llm():
            code = "TIME_BUDGET_EXCEEDED" if self.remaining_s <= 0 else "LLM_TURN_LIMIT"
            raise AppError(ErrorCode.BUDGET_EXCEEDED, f"{code} before {node}", {"code": code, **self.snapshot()})
        self.llm_turns += 1

    def start_tool(self, name: str) -> None:
        if self.remaining_s <= 0:
            raise AppError(ErrorCode.BUDGET_EXCEEDED, "TIME_BUDGET_EXCEEDED", {"code": "TIME_BUDGET_EXCEEDED"})
        if self.tool_calls[name] >= self.max_same_tool_calls:
            raise AppError(
                ErrorCode.BUDGET_EXCEEDED,
                f"tool {name} call limit ({self.max_same_tool_calls}) reached",
                {"code": "TOOL_CALL_LIMIT", "tool": name},
            )
        self.tool_calls[name] += 1

    def charge(self, tokens: int, usd: float) -> None:
        self.tokens += tokens
        self.usd += usd

    def snapshot(self) -> dict[str, Any]:
        return {
            "llm_turns": self.llm_turns,
            "provider_request_count": self.provider_request_count,
            "active_elapsed_ms": int(self.active_s * 1000),
            "repair_count": self.repair_count,
            "tool_calls": dict(self.tool_calls),
            "tokens": self.tokens,
            "usd": round(self.usd, 6),
        }

    def restore(self, snap: dict[str, Any]) -> None:
        """승인 후 재개: 누적값을 이어받는다 (초기화 금지)."""
        self.llm_turns = int(snap.get("llm_turns", 0))
        self.provider_request_count = int(snap.get("provider_request_count", 0))
        self.active_elapsed_ms = int(snap.get("active_elapsed_ms", 0))
        self.repair_count = int(snap.get("repair_count", 0))
        self.tool_calls = Counter(snap.get("tool_calls", {}))
        self.tokens = int(snap.get("tokens", 0))
        self.usd = float(snap.get("usd", 0.0))
