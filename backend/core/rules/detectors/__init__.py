"""규칙 detector 구현. detector 이름 → 함수."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from core.rules.models import Rule, RuleHit
from harness.masking import mask_text

# ---------- 공통 ----------

_COMMENT_MARKERS = ("//", "#", "/*", "*", "<!--", "--")


def comment_part(line: str, lang: str) -> str | None:
    """라인의 주석 부분 (없으면 None). 문자열 안의 // 는 단순 휴리스틱으로 제외."""
    s = line.strip()
    if lang == "markdown":
        m = re.search(r"<!--(.*?)(-->|$)", line)
        return m.group(1) if m else line  # 문서는 전체를 주석으로 본다
    if lang in ("yaml", "properties", "shell", "env"):
        idx = line.find("#")
        return line[idx + 1 :] if idx >= 0 else None
    if s.startswith(("*", "/*")):
        return s.lstrip("/*").strip()
    in_str = False
    for i, ch in enumerate(line):
        if ch == '"' and (i == 0 or line[i - 1] != "\\"):
            in_str = not in_str
        if not in_str and line.startswith("//", i):
            return line[i + 2 :]
    return None


def lang_of(path: str) -> str:
    p = path.lower()
    name = p.rsplit("/", 1)[-1]
    if p.endswith((".yml", ".yaml")):
        return "yaml"
    if p.endswith(".md"):
        return "markdown"
    if p.endswith(".properties"):
        return "properties"
    if name == ".env" or name.startswith(".env."):
        return "env"
    if p.endswith((".sh", ".py")):
        return "shell"
    return "java"


def hit(rule: Rule, path: str, line_no: int, line: str, **extra: Any) -> RuleHit:
    return RuleHit(
        rule_id=rule.id,
        category=rule.category,
        severity=rule.default_severity,
        path=path,
        line=line_no,
        snippet_masked=mask_text(line.strip())[:300],
        needs_llm_review=rule.needs_llm_review,
        references=rule.references,
        fix_hint=rule.fix_hint,
        extra=extra,
    )


Detector = Callable[[Rule, str, str], list[RuleHit]]


# ---------- regex ----------


def detect_regex(rule: Rule, path: str, content: str) -> list[RuleHit]:
    flags = re.IGNORECASE if rule.ignore_case else 0
    pats = [re.compile(p, flags) for p in rule.all_patterns()]
    if rule.require_also and not re.search(rule.require_also, content, flags):
        return []
    lang = lang_of(path)
    out: list[RuleHit] = []
    for i, line in enumerate(content.splitlines(), start=1):
        text = comment_part(line, lang) if rule.comment_only else line
        if not text:
            continue
        if any(p.search(text) for p in pats):
            out.append(hit(rule, path, i, line))
    return out


# ---------- entropy ----------

_QUOTED = re.compile(r'"([^"\\]{8,})"')


def shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    counts = Counter(s)
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in counts.values())


def detect_entropy(rule: Rule, path: str, content: str) -> list[RuleHit]:
    name_re = re.compile(rule.name_pattern or r"(?i)(key|secret|token)")
    out: list[RuleHit] = []
    for i, line in enumerate(content.splitlines(), start=1):
        for m in _QUOTED.finditer(line):
            value = m.group(1)
            lhs = line[: m.start()]
            ident = re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*[=:(]\s*$", lhs)
            if not ident or not name_re.search(ident[-1]):
                continue
            ent = shannon_entropy(value)
            if len(value) >= rule.min_length and ent >= rule.min_entropy:
                out.append(hit(rule, path, i, line, identifier=ident[-1], entropy=round(ent, 2)))
    return out


# ---------- yaml_path ----------


def _flatten_yaml(content: str) -> list[tuple[str, Any, int]]:
    """(dotted.path, scalar value, 1-based line) 목록."""
    try:
        root = yaml.compose(content)
    except yaml.YAMLError:
        return []
    out: list[tuple[str, Any, int]] = []

    def walk(node: yaml.Node, prefix: str) -> None:
        if isinstance(node, yaml.MappingNode):
            for k, v in node.value:
                walk(v, f"{prefix}.{k.value}" if prefix else str(k.value))
        elif isinstance(node, yaml.SequenceNode):
            for idx, v in enumerate(node.value):
                walk(v, f"{prefix}[{idx}]")
        elif isinstance(node, yaml.ScalarNode):
            value: Any = yaml.safe_load(yaml.serialize(node))
            out.append((prefix, value, node.start_mark.line + 1))

    if root is not None:
        walk(root, "")
    return out


def _path_match(pattern: str, path: str) -> bool:
    regex = "^" + re.escape(pattern).replace(r"\*", r"[^.]+(?:\.[^.]+)*") + "$"
    return re.match(regex, path) is not None


def detect_yaml_path(rule: Rule, path: str, content: str) -> list[RuleHit]:
    lines = content.splitlines()
    out: list[RuleHit] = []
    for key, value, line_no in _flatten_yaml(content):
        if not any(_path_match(p, key) for p in rule.paths):
            continue
        if rule.plaintext_only:
            if value is None or str(value).strip() == "" or re.search(r"\$\{[^}]+\}", str(value)):
                continue
        if rule.equals is not None and value != rule.equals and str(value) != str(rule.equals):
            continue
        out.append(hit(rule, path, line_no, lines[line_no - 1] if line_no <= len(lines) else key, key=key))
    return out


# ---------- python (파일 단위 검사) ----------


def detect_python(rule: Rule, path: str, content: str) -> list[RuleHit]:
    if rule.check == "sensitive_file_present":
        name = Path(path).name
        pats = rule.params.get("names", [".env", "*.pem", "*.p12", "*.jks", "*.key"])
        if any(Path(name).match(p) for p in pats):
            first = content.splitlines()[0] if content else name
            return [hit(rule, path, 1, first, reason="sensitive file committed")]
    return []


DETECTORS: dict[str, Detector] = {
    "regex": detect_regex,
    "entropy": detect_entropy,
    "yaml_path": detect_yaml_path,
    "python": detect_python,
}
