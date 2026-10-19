"""규칙 YAML 형식 (명세 7.1)."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from core.settings import get_settings

Severity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
SEVERITY_ORDER: dict[str, int] = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "INFO": 0}


class Rule(BaseModel):
    id: str
    name: str
    category: str
    default_severity: Severity
    target: Literal["code", "agent"]
    file_globs: list[str] = Field(default_factory=lambda: ["**/*"])
    exclude_globs: list[str] = Field(default_factory=list)
    detector: Literal["regex", "entropy", "ast_java", "yaml_path", "manifest", "python"]
    # regex
    pattern: str | None = None
    patterns: list[str] = Field(default_factory=list)
    require_also: str | None = None  # 같은 파일에 이 패턴도 있어야 함 (예: CORS allowCredentials)
    comment_only: bool = False
    ignore_case: bool = False
    # entropy
    min_entropy: float = 4.0
    min_length: int = 20
    name_pattern: str | None = None
    # yaml_path
    paths: list[str] = Field(default_factory=list)
    equals: Any = None
    plaintext_only: bool = False
    # python / manifest
    check: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    # 공통
    suppressed_by: list[str] = Field(default_factory=list)  # 같은 라인에 이 규칙이 잡히면 생략
    needs_llm_review: bool = False
    references: list[str] = Field(default_factory=list)
    fix_hint: str | None = None

    def all_patterns(self) -> list[str]:
        return ([self.pattern] if self.pattern else []) + self.patterns


class RuleHit(BaseModel):
    rule_id: str
    category: str
    severity: Severity
    path: str
    line: int
    snippet_masked: str
    needs_llm_review: bool = False
    references: list[str] = Field(default_factory=list)
    fix_hint: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


def load_rules_from(directory: Path) -> list[Rule]:
    rules: list[Rule] = []
    for path in sorted(directory.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        items = data if isinstance(data, list) else [data]
        rules.extend(Rule.model_validate(x) for x in items if x)
    return rules


@lru_cache
def load_rules(target: Literal["code", "agent"]) -> tuple[Rule, ...]:
    return tuple(load_rules_from(get_settings().rules_dir / target))


def rules_hash(rules: tuple[Rule, ...] | list[Rule]) -> str:
    blob = "".join(r.model_dump_json() for r in rules)
    return hashlib.sha1(blob.encode()).hexdigest()[:12]  # noqa: S324 - 캐시 키 용도
