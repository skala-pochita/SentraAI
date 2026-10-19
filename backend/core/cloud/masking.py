"""Host 경계 마스킹 (기획서 6장 Guardrails). 모델·ToolMessage·SSE·trace 에 가기 전에 적용한다.

- 이메일·전화번호는 일관 치환(<email-1>)해 같은 값이 같은 토큰으로 남는다.
- 비밀값은 harness.masking 의 앞 4자 + **** 규칙.
- 근거·역할·자원 ID 필드는 건드리지 않아 B1 연결이 끊기지 않는다 (`mask_payload` 는 ID 키를 보존).
"""

from __future__ import annotations

import re
from typing import Any

from harness.masking import mask_text

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<!\d)(?:\+?82[-\s]?)?0?1[0-9][-\s]?\d{3,4}[-\s]?\d{4}(?!\d)")
ID_KEYS = frozenset(
    {"id", "tool_id", "principal_id", "policy_id", "resource_id", "source_ref", "evidence_id", "evidence_ids", "trust"}
)


class Masker:
    """한 작업 안에서 같은 원문 → 같은 치환 토큰."""

    def __init__(self) -> None:
        self._map: dict[str, str] = {}

    def _sub(self, kind: str, value: str) -> str:
        key = f"{kind}:{value}"
        if key not in self._map:
            self._map[key] = f"<{kind}-{sum(1 for k in self._map if k.startswith(kind + ':')) + 1}>"
        return self._map[key]

    def text(self, text: str) -> str:
        text = _EMAIL.sub(lambda m: self._sub("email", m.group(0)), text)
        text = _PHONE.sub(lambda m: self._sub("phone", m.group(0)), text)
        return mask_text(text)

    def payload(self, obj: Any, key: str | None = None) -> Any:
        if key in ID_KEYS:
            return obj
        if isinstance(obj, str):
            return self.text(obj)
        if isinstance(obj, dict):
            return {k: self.payload(v, k) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.payload(v, key) for v in obj]
        return obj
