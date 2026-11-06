"""코드 감사 카테고리: 정적 규칙 카테고리 ∪ config/categories.yaml (LLM 새 발견용, DECISIONS 2026-11-06)."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from core.settings import load_config


@lru_cache
def extra_code_categories() -> dict[str, dict[str, Any]]:
    """규칙이 없는(또는 규칙만으로 잡기 어려운) 카테고리 {name: {description, severity, refs}}."""
    return dict(load_config("categories").get("code", {}) or {})


@lru_cache
def code_categories() -> frozenset[str]:
    from core.rules.models import load_rules

    return frozenset({r.category for r in load_rules("code")} | set(extra_code_categories()))


def category_refs(category: str) -> list[str]:
    """카테고리의 기본 OWASP/CWE 참조 (규칙 카테고리는 해당 규칙들의 references 합집합)."""
    from core.rules.models import load_rules

    if category in extra_code_categories():
        return list(extra_code_categories()[category].get("refs", []))
    refs: set[str] = set()
    for r in load_rules("code"):
        if r.category == category:
            refs.update(r.references)
    return sorted(refs)


def category_definitions() -> str:
    """프롬프트용 카테고리 정의 목록."""
    from core.rules.models import load_rules

    names: dict[str, list[str]] = {}
    for r in load_rules("code"):
        names.setdefault(r.category, []).append(r.name)
    for c, meta in extra_code_categories().items():
        names.setdefault(c, []).append(str(meta.get("description", "")))
    return "\n    ".join(f"- {c}: {' / '.join(n)}" for c, n in sorted(names.items()))
