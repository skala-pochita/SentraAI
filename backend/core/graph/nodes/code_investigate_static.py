"""정적 후보 판정 (LLM 없이 규칙 기준으로 확정)."""

from __future__ import annotations

from typing import Any

from core.schemas import FindingDraft


def static_verdicts(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for c in candidates:
        f = FindingDraft.model_validate(c)
        if f.category == "AUDITOR_INJECTION_ATTEMPT":
            f.verdict, f.confidence = "TRUE_POSITIVE", 0.9
            f.reasoning_summary = "감사기를 겨냥한 지시문이 대상 텍스트에 있다. 지시로 따르지 않고 보고한다."
        elif f.confidence >= 0.8:
            f.verdict = "TRUE_POSITIVE"
        out.append(f.model_dump())
    return out
