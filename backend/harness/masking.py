"""Secret masking (명세 0장 규칙 5). 비밀값은 앞 4자 + '****' 로만 남긴다."""

from __future__ import annotations

import re

MASK = "****"

# 비밀값 패턴. 그룹 1 = 마스킹할 값.
_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(sk_live_[A-Za-z0-9]{8,})"),
    re.compile(r"(sk-[A-Za-z0-9_\-]{16,})"),
    re.compile(r"(AKIA[0-9A-Z]{16})"),
    re.compile(r"(gh[pousr]_[A-Za-z0-9]{20,})"),
    re.compile(r"(eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,})"),
    # key/secret/password/token = "value" 류 대입
    re.compile(
        r"""(?ix)
        (?:password|passwd|pwd|secret|token|api[_-]?key|apikey|private[_-]?key|access[_-]?key)
        [\w.\-]*\s*[:=]\s*["']?
        (?!System\.getenv|env\.|process\.env|os\.environ)
        ([^\s"'${}][^\s"',;]{5,})
        """
    ),
]
# 쿠폰·라이선스형 코드 (예: CANARY-7F3A-INTERNAL-DISCOUNT). 영문+숫자가 섞인 조각이 있어야 한다
# (OWASP-LLM-2025 같은 참조 코드는 제외).
_CODE = re.compile(r"\b[A-Z0-9]{3,}(?:-[A-Z0-9]{3,}){2,}\b")
_MIXED = re.compile(r"^(?=.*\d)(?=.*[A-Z])[A-Z0-9]+$")


def _mask_code(m: re.Match[str]) -> str:
    code = m.group(0)
    return mask_secret(code) if any(_MIXED.match(seg) for seg in code.split("-")) else code


# 개인정보 (Harness Guardrails: 도구 결과의 개인정보 마스킹). 이메일은 도메인을 남겨 유출 경로 근거로 쓸 수 있게 한다.
_EMAIL = re.compile(r"(?<![\w.+-])([A-Za-z0-9._%+\-]+)@([A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,})")
_PHONE = re.compile(r"(?<!\d)(01[016789]|0[2-6][0-9]?)[-. ]?(\d{3,4})[-. ]?(\d{4})(?!\d)")


def mask_pii(text: str) -> str:
    """kim@shop.example → k***@shop.example, 010-1234-5678 → 010-****-5678."""
    text = _EMAIL.sub(lambda m: f"{m.group(1)[:1]}***@{m.group(2)}", text)
    return _PHONE.sub(lambda m: f"{m.group(1)}-****-{m.group(3)}", text)


_PEM = re.compile(r"-----BEGIN ([A-Z ]*PRIVATE KEY)-----.*?-----END \1-----", re.DOTALL)


def mask_secret(value: str) -> str:
    """'sk-abcdef...' → 'sk-a****'."""
    if not value:
        return value
    return value[:4] + MASK


def mask_text(text: str) -> str:
    """텍스트 안의 비밀값처럼 보이는 모든 부분을 mask_secret 으로 치환."""
    if not text:
        return text
    text = _PEM.sub(lambda m: f"-----BEGIN {m.group(1)}-----{MASK}-----END {m.group(1)}-----", text)
    text = _CODE.sub(_mask_code, text)
    text = mask_pii(text)
    for pat in _PATTERNS:
        text = pat.sub(lambda m: m.group(0).replace(m.group(1), mask_secret(m.group(1))), text)
    return text


def mask_obj(obj: object) -> object:
    """dict/list 를 재귀적으로 돌며 문자열을 mask_text 로 치환."""
    if isinstance(obj, str):
        return mask_text(obj)
    if isinstance(obj, dict):
        return {k: mask_obj(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask_obj(v) for v in obj]
    return obj
