"""H4 가드레일.

- 입력: 스캔 텍스트의 인젝션 패턴 탐지 → AUDITOR_INJECTION_ATTEMPT
- 출력: Pydantic 스키마 검증 + 재요청 (harness/llm.structured_call)
- 근거 검증: 파일·라인 실재 확인
- 비밀값 마스킹 (harness/masking)
"""

from __future__ import annotations

import re
from typing import Any

from core.schemas import normalize_snippet

INJECTION_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignore (?:all )?previous instructions",
        r"\bAI[ _-]?auditor\b",
        r"report no (?:issues|findings)",
        r"empty findings",
        r"이슈를? 보고하지 말",
        r"이전 지시(?:는|를)? (?:모두 )?무시",
        r"disregard (?:the )?above",
    )
]


def detect_injection(text: str) -> list[tuple[int, str]]:
    """(라인 번호, 라인) — 감사기를 겨냥한 지시문 후보."""
    out = []
    for i, line in enumerate(text.splitlines(), start=1):
        if any(p.search(line) for p in INJECTION_PATTERNS):
            out.append((i, line.strip()))
    return out


def evidence_matches(content_masked: str, ev: dict[str, Any], tolerance: int = 2) -> bool:
    """evidence 의 라인 범위(±tolerance)에 snippet 이 실제로 있는지."""
    if not content_masked:
        return False
    lines = content_masked.splitlines()
    start = int(ev.get("line_start") or 0)
    end = int(ev.get("line_end") or start)
    if start < 1 or start > len(lines):
        return False
    snippet = normalize_snippet(ev.get("snippet_masked"))
    if not snippet:
        return True
    lo, hi = max(1, start - tolerance), min(len(lines), end + tolerance)
    window = normalize_snippet(" ".join(lines[lo - 1 : hi]))
    # 마스킹된 값(****) 이전까지만 비교해 마스킹 차이에 견고하게
    probe = snippet.split("****")[0][:80]
    return probe in window
