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
    reserved_tokens: int = 0  # 진행 중인 LLM 호출이 미리 잡아 둔 양 (병렬 호출 예산 초과 방지)
    reserved_usd: float = 0.0
    reserve_output_tokens: int = 2000  # 호출 1회 출력 토큰 예상치 (예약용)
    events: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_config(cls) -> Budget:
        a = load_config("budget").get("audit", {})
        return cls(
            int(a.get("max_tokens", 400000)),
            float(a.get("max_cost_usd", 2.0)),
            reserve_output_tokens=int(a.get("reserve_output_tokens", 2000)),
        )

    def restore(self, usage: list[dict[str, Any]]) -> None:
        """체크포인트 재개·Kafka 재시도 시 이미 쓴 토큰·비용을 다시 반영한다 (state.token_usage 합계)."""
        self.used_tokens = sum(int(u.get("in", 0) or 0) + int(u.get("out", 0) or 0) for u in usage)
        self.used_usd = sum(float(u.get("usd", 0) or 0) for u in usage)

    def charge(self, tokens: int, usd: float) -> None:
        self.used_tokens += tokens
        self.used_usd += usd

    def reserve(self, tokens: int, usd: float) -> bool:
        """LLM 호출 전에 예상 사용량을 잡아 둔다. 이미 쓴 양 + 진행 중 예약 + 이번 예상이 상한을 넘으면 False.

        await 가 없는 동기 메서드라 asyncio 에서 확인과 예약 사이에 다른 호출이 끼어들 수 없다.
        예전처럼 '쓴 양 < 상한' 만 보고 호출하면, 동시에 N개가 모두 통과해 최대 N회분 상한을 넘었다 (B1 병렬화 부작용)."""
        if self.exceeded():
            return False
        if self.used_tokens + self.reserved_tokens + tokens > self.max_tokens:
            return False
        if self.max_cost_usd > 0 and self.used_usd + self.reserved_usd + usd > self.max_cost_usd:
            return False
        self.reserved_tokens += tokens
        self.reserved_usd += usd
        return True

    def release(self, tokens: int, usd: float) -> None:
        """호출이 끝나면(성공·실패 모두) 예약을 푼다. 성공했으면 그 전에 charge 로 실제 사용량을 반영한다."""
        self.reserved_tokens = max(0, self.reserved_tokens - tokens)
        self.reserved_usd = max(0.0, self.reserved_usd - usd)

    def exceeded(self) -> bool:
        return self.used_tokens >= self.max_tokens or (self.max_cost_usd > 0 and self.used_usd >= self.max_cost_usd)

    def snapshot(self) -> dict[str, Any]:
        return {
            "used_tokens": self.used_tokens,
            "used_usd": round(self.used_usd, 6),
            "max_tokens": self.max_tokens,
            "max_cost_usd": self.max_cost_usd,
            "reserved_tokens": self.reserved_tokens,
        }


def limits() -> dict[str, Any]:
    return load_config("budget")
