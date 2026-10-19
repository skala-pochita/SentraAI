"""H8 예산 제어: 감사당 최대 토큰·비용, ReAct 최대 스텝, probe 최대 개수. 초과 시 해당 단계 조기 종료 + 이벤트."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.settings import load_config


@dataclass
class Budget:
    max_tokens: int
    max_cost_usd: float
    used_tokens: int = 0
    used_usd: float = 0.0
    events: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_config(cls) -> Budget:
        a = load_config("budget").get("audit", {})
        return cls(int(a.get("max_tokens", 400000)), float(a.get("max_cost_usd", 2.0)))

    def charge(self, tokens: int, usd: float) -> None:
        self.used_tokens += tokens
        self.used_usd += usd

    def exceeded(self) -> bool:
        return self.used_tokens >= self.max_tokens or (self.max_cost_usd > 0 and self.used_usd >= self.max_cost_usd)

    def snapshot(self) -> dict[str, Any]:
        return {
            "used_tokens": self.used_tokens,
            "used_usd": round(self.used_usd, 6),
            "max_tokens": self.max_tokens,
            "max_cost_usd": self.max_cost_usd,
        }


def limits() -> dict[str, Any]:
    return load_config("budget")
