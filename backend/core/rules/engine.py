"""규칙 엔진: 코드 정적 스캔 (7.2) + Agent 매니페스트 규칙 (7.3)."""

from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import Any

from core.rules.detectors import DETECTORS, hit, shannon_entropy
from core.rules.models import Rule, RuleHit, load_rules
from harness.masking import mask_text

SKIP_DIRS = {".git", "build", "target", "node_modules", ".gradle", ".idea", "out"}
MAX_FILE_BYTES = 512 * 1024
TEST_PATH = re.compile(r"(^|/)src/test/")


@lru_cache(maxsize=256)
def _glob_regex(glob: str) -> re.Pattern[str]:
    out = ""
    i = 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif glob.startswith("**", i):
            out += ".*"
            i += 2
        elif glob[i] == "*":
            out += "[^/]*"
            i += 1
        elif glob[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(glob[i])
            i += 1
    return re.compile(f"^{out}$")


def glob_match(path: str, globs: Iterable[str]) -> bool:
    return any(_glob_regex(g).match(path) for g in globs)


def iter_repo_files(root: Path) -> Iterable[tuple[str, Path]]:
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts) or not p.is_file():
            continue
        if p.stat().st_size > MAX_FILE_BYTES:
            continue
        yield rel.as_posix(), p


def read_text(p: Path) -> str | None:
    try:
        data = p.read_bytes()
    except OSError:
        return None
    if b"\x00" in data[:4096]:
        return None
    return data.decode("utf-8", errors="replace")


def scan_files(files: dict[str, str], rules: Iterable[Rule]) -> list[RuleHit]:
    """files: {rel_path: content}. 규칙 적용 후 suppressed_by·테스트 경로 처리."""
    rules = [r for r in rules if r.target == "code"]
    hits: list[RuleHit] = []
    for path, content in files.items():
        for rule in rules:
            if not glob_match(path, rule.file_globs) or glob_match(path, rule.exclude_globs):
                continue
            detector = DETECTORS.get(rule.detector)
            if detector is None:
                continue
            hits.extend(detector(rule, path, content))
    by_line = {(h.path, h.line, h.rule_id) for h in hits}
    rule_map = {r.id: r for r in rules}
    out: list[RuleHit] = []
    for h in hits:
        r = rule_map[h.rule_id]
        if any((h.path, h.line, s) in by_line for s in r.suppressed_by):
            continue
        if TEST_PATH.search(h.path):  # 테스트 리소스는 참고(INFO)로만
            h = h.model_copy(update={"severity": "INFO", "extra": {**h.extra, "test_path": True}})
        out.append(h)
    out.sort(key=lambda h: (h.path, h.line, h.rule_id))
    return out


def scan_repo(root: Path, paths: list[str] | None = None, rule_ids: list[str] | None = None) -> list[RuleHit]:
    rules = [r for r in load_rules("code") if not rule_ids or r.id in rule_ids]
    files: dict[str, str] = {}
    for rel, p in iter_repo_files(root):
        if paths and rel not in paths:
            continue
        text = read_text(p)
        if text is not None:
            files[rel] = text
    return scan_files(files, rules)


# ---------------- Agent 규칙 (7.3) ----------------

HIERARCHY_PHRASES = (
    "데이터로",
    "지시가 아니다",
    "지시로 취급하지",
    "따르지 않는다",
    "untrusted",
    "지시 계층",
    "우선순위",
    "treat as data",
    "do not follow instructions",
)
SECRET_PHRASES = ("공개 금지", "절대 공개", "비밀", "기밀", "do not reveal", "never disclose")


def _manifest_line(manifest_text: str, tool: str | None) -> int:
    if tool is None:
        return 1
    for i, line in enumerate(manifest_text.splitlines(), start=1):
        if re.search(rf"name:\s*{re.escape(tool)}\s*$", line):
            return i
    return 1


def _tool_hit(
    rule: Rule, manifest_path: str, manifest_text: str, tool: dict[str, Any], why: str, severity: str | None = None
) -> RuleHit:
    line = _manifest_line(manifest_text, tool["name"])
    h = hit(rule, manifest_path, line, f"- name: {tool['name']}", tool=tool["name"], reason=why)
    return h.model_copy(update={"severity": severity}) if severity else h


def check_manifest(
    manifest: dict[str, Any],
    manifest_text: str,
    system_prompt: str,
    code_secret_names: set[str] | None = None,
    manifest_path: str = "manifest.yaml",
    prompt_path: str = "system_prompt.md",
) -> list[RuleHit]:
    rules = {r.check: r for r in load_rules("agent") if r.check}
    tools: list[dict[str, Any]] = manifest.get("tools", [])
    out: list[RuleHit] = []

    if r := rules.get("tool_no_approval"):
        for t in tools:
            if t.get("side_effect") in ("write", "delete", "external_send") and not t.get("requires_approval"):
                sev = "CRITICAL" if t.get("side_effect") == "delete" else None
                out.append(
                    _tool_hit(
                        r,
                        manifest_path,
                        manifest_text,
                        t,
                        f"side_effect={t.get('side_effect')} 인데 requires_approval=false",
                        sev,
                    )
                )
    if r := rules.get("tool_excessive_scope"):
        for t in tools:
            scopes = [str(s) for s in t.get("scopes", [])]
            wildcard = [s for s in scopes if "*" in s]
            write_on_read = t.get("side_effect") == "read" and any(":write" in s for s in scopes)
            if (wildcard or write_on_read) and not (
                t.get("side_effect") == "external_send" and all(s.endswith(":any") for s in scopes)
            ):
                out.append(_tool_hit(r, manifest_path, manifest_text, t, f"과도한 스코프 {scopes}"))
    if r := rules.get("tool_exfil_channel"):
        for t in tools:
            scopes = [str(s) for s in t.get("scopes", [])]
            if t.get("side_effect") == "external_send" and not any(":domain:" in s for s in scopes):
                out.append(_tool_hit(r, manifest_path, manifest_text, t, f"수신자 제한 없음 {scopes}"))
    if r := rules.get("indirect_injection_surface"):
        for t in tools:
            if t.get("input_source") == "external" and not t.get("output_untrusted"):
                out.append(_tool_hit(r, manifest_path, manifest_text, t, "외부 입력을 untrusted 표시 없이 반환"))

    plines = system_prompt.splitlines()
    if r := rules.get("prompt_secret_embedded"):
        for i, line in enumerate(plines, start=1):
            tokens = re.findall(r"[A-Za-z0-9_\-]{12,}", line)
            secretish = any(shannon_entropy(tk) >= 3.5 for tk in tokens)
            if tokens and (secretish and any(ph in line for ph in SECRET_PHRASES)):
                out.append(hit(r, prompt_path, i, line, target="system_prompt"))
    if r := rules.get("prompt_no_instruction_hierarchy"):
        if not any(ph in system_prompt for ph in HIERARCHY_PHRASES):
            first = plines[0] if plines else ""
            out.append(
                hit(
                    r,
                    prompt_path,
                    1,
                    first,
                    target="system_prompt",
                    reason="사용자·문서 지시를 데이터로 취급하라는 규칙 없음 (LLM 재판정 대상)",
                )
            )
    if (r := rules.get("agent_shared_secret")) and code_secret_names:
        for name in manifest.get("secrets_env", []) or []:
            if name in code_secret_names:
                ln = next((i for i, s in enumerate(manifest_text.splitlines(), 1) if "secrets_env" in s), 1)
                out.append(hit(r, manifest_path, ln, mask_text(f"secrets_env: [{name}]"), env=name))
    return out


SECRET_CATEGORIES = {"SECRET_LEAK", "HARDCODED_SECRET", "HARDCODED_CREDENTIAL"}


def secret_names_from_hits(hits: Iterable[RuleHit]) -> set[str]:
    """코드에서 하드코딩 비밀이 대입된 식별자 이름 (AGT-SECRET-001 입력)."""
    names: set[str] = set()
    for h in hits:
        if h.category not in SECRET_CATEGORIES:
            continue
        if ident := h.extra.get("identifier"):
            names.add(str(ident))
        names.update(re.findall(r"\b([A-Z][A-Z0-9_]{3,})\s*[=:]", h.snippet_masked))
    return names
