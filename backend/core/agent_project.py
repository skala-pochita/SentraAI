"""Discover uploaded agent definitions without importing or executing project code."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import yaml

from core.errors import AppError, ErrorCode
from core.rules.engine import iter_repo_files, read_text
from core.schemas import Evidence, FindingDraft
from harness.masking import mask_text

TOOL_LIST_NAMES = {"TOOL_SCHEMAS", "TOOLS", "tools"}
PROMPT_NAMES = {"SYSTEM", "SYSTEM_PROMPT", "system_prompt"}
SECRET_TERMS = ("SERVICE_TOKEN", "API_KEY", "PASSWORD", "SECRET")
TOOL_POLICY_REASON = (
    "소스에서 호출 가능한 도구 정의를 발견했으나 이 정의에는 접근 범위·승인 정책의 증거가 없습니다. "
    "런타임 인증·인가 코드를 함께 검토해야 합니다. 취약점 확정이 아닌 검토 대상입니다."
)
PROMPT_SECRET_REASON = (
    "시스템 프롬프트 구성식이 자격증명 변수를 참조합니다. 실제 전송값과 변수 해석은 실행 환경 확인이 필요합니다."
)


def _probe_set(files: dict[str, Path]) -> dict[str, Any] | None:
    if "audit-probes.yaml" not in files:
        return None
    from core.probes import ProbeSet

    try:
        return ProbeSet.model_validate(yaml.safe_load(files["audit-probes.yaml"].read_text())).model_dump()
    except (ValueError, yaml.YAMLError) as exc:
        raise AppError(ErrorCode.VALIDATION, "audit-probes.yaml 공격 정의 형식 오류") from exc


def _declared_protocol(files: dict[str, Path]) -> str | None:
    if "audit-runtime.yaml" not in files:
        return None
    try:
        settings = yaml.safe_load(files["audit-runtime.yaml"].read_text())
    except yaml.YAMLError as exc:
        raise AppError(ErrorCode.VALIDATION, "audit-runtime.yaml 형식 오류") from exc
    if not isinstance(settings, dict) or settings.get("protocol") not in {"atlas", "auditor"}:
        raise AppError(ErrorCode.VALIDATION, "protocol은 atlas 또는 auditor여야 합니다.")
    return str(settings["protocol"])


def _with_probes(manifest: dict[str, Any], probe_set: dict[str, Any] | None) -> dict[str, Any]:
    return manifest | ({"_probe_set": probe_set} if probe_set else {})


def _explicit_manifest(
    files: dict[str, Path], probe_set: dict[str, Any] | None, protocol: str | None
) -> dict[str, Any] | None:
    """명시적인 manifest.yaml(tools 목록)이 있으면 소스 추론보다 우선한다. ZIP 의 런타임 URL 은 따르지 않는다."""
    for rel, path in files.items():
        if path.name not in {"manifest.yaml", "manifest.yml"}:
            continue
        raw = read_text(path) or ""
        try:
            manifest = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise AppError(ErrorCode.VALIDATION, f"에이전트 매니페스트 YAML 오류: {rel}") from exc
        if not isinstance(manifest, dict) or not isinstance(manifest.get("tools"), list):
            continue
        if any(not isinstance(t, dict) or not isinstance(t.get("name"), str) for t in manifest["tools"]):
            raise AppError(ErrorCode.VALIDATION, f"에이전트 도구 형식 오류: {rel}")
        prompt_rel = (Path(rel).parent / "system_prompt.md").as_posix()
        prompt = read_text(files[prompt_rel]) if prompt_rel in files else ""
        return {
            "kind": "manifest",
            "manifest": _with_probes(manifest, probe_set),
            "runtime_protocol": protocol or "auditor",
            "manifest_path": rel,
            "manifest_text": raw,
            "system_prompt": prompt or "",
            "prompt_path": prompt_rel,
            "source_findings": [],
            "discovery": "explicit_manifest",
        }
    return None


def _literal_functions(value: ast.expr) -> list[dict[str, Any]]:
    """TOOLS = [...] 리터럴에서 {name, description, parameters} 함수 정의만 꺼낸다 (import·실행 없음)."""
    try:
        literal = ast.literal_eval(value)
    except (ValueError, TypeError, SyntaxError, RecursionError):
        return []
    if not isinstance(literal, list):
        return []
    out = []
    for item in literal:
        function = item.get("function", item) if isinstance(item, dict) else None
        if isinstance(function, dict) and isinstance(function.get("name"), str):
            out.append(function)
    return out


class _SourceScan:
    def __init__(self) -> None:
        self.tools: list[dict[str, Any]] = []
        self.prompts: list[str] = []
        self.findings: list[dict[str, Any]] = []
        self.tool_files: list[str] = []
        self.atlas = False

    def add_tools(self, rel: str, body: str, node: ast.stmt, value: ast.expr) -> None:
        for function in _literal_functions(value):
            name = function["name"]
            self.tools.append(
                {
                    "name": name,
                    "description": str(function.get("description", "")),
                    "parameters": function.get("parameters", {}),
                    "policy_status": "UNKNOWN",
                    "source_path": rel,
                    "source_line": node.lineno,
                }
            )
            self.tool_files.append(rel)
            self.findings.append(
                {
                    "category": "TOOL_ACCESS_POLICY_UNKNOWN",
                    "severity": "MEDIUM",
                    "path": rel,
                    "line": node.lineno,
                    "snippet": ast.get_source_segment(body, node) or "",
                    "title": f"도구 권한·승인 정책 확인 필요: {name}",
                    "reason": TOOL_POLICY_REASON,
                }
            )

    def add_prompt(self, rel: str, body: str, node: ast.stmt, value: ast.expr) -> None:
        segment = ast.get_source_segment(body, value) or ""
        self.prompts.append(segment)
        if any(term in segment.upper() for term in SECRET_TERMS):
            self.findings.append(
                {
                    "category": "PROMPT_SECRET_EMBEDDED",
                    "severity": "MEDIUM",
                    "path": rel,
                    "line": node.lineno,
                    "snippet": segment,
                    "title": "시스템 프롬프트의 자격증명 참조",
                    "reason": PROMPT_SECRET_REASON,
                }
            )

    def scan_file(self, rel: str, body: str) -> None:
        if "/sessions" in body and "/chat" in body and "session_id" in body:
            self.atlas = True
        try:
            tree = ast.parse(body)
        except (SyntaxError, RecursionError):
            return
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)) or node.value is None:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = {n.id for n in targets if isinstance(n, ast.Name)}
            if names & TOOL_LIST_NAMES:
                self.add_tools(rel, body, node, node.value)
            if names & PROMPT_NAMES:
                self.add_prompt(rel, body, node, node.value)


def discover_agent(root: Path) -> dict[str, Any] | None:
    root = root.resolve()
    files = dict(iter_repo_files(root))
    probe_set = _probe_set(files)
    protocol = _declared_protocol(files)
    if (explicit := _explicit_manifest(files, probe_set, protocol)) is not None:
        return explicit
    scan = _SourceScan()
    for rel, path in files.items():
        if rel.endswith(".py"):
            scan.scan_file(rel, read_text(path) or "")
    if not scan.tools:
        return None
    return {
        "kind": "source",
        "runtime_protocol": protocol or ("atlas" if scan.atlas else "unknown"),
        "manifest": _with_probes({"tools": scan.tools, "policy_status": "UNKNOWN"}, probe_set),
        "manifest_path": scan.tool_files[0],
        "manifest_text": yaml.safe_dump({"tools": scan.tools}, allow_unicode=True),
        "system_prompt": "\n".join(scan.prompts),
        "prompt_path": scan.tool_files[0],
        "source_findings": scan.findings,
        "discovery": "python_literal_tools",
    }


def source_drafts(profile: dict[str, Any], target_id: str) -> list[dict[str, Any]]:
    return [
        FindingDraft(
            temp_id=f"source-agent-{i}",
            target_id=target_id,
            source="agent",
            category=f["category"],
            rule_id="AGENT-SOURCE-POLICY",
            title=f["title"],
            severity=f["severity"],
            confidence=0.6,
            verdict="NEEDS_REVIEW",
            evidence=[
                Evidence(
                    kind="code",
                    file_path=f["path"],
                    line_start=f["line"],
                    line_end=f["line"],
                    snippet_masked=mask_text(f["snippet"])[:2000],
                )
            ],
            reasoning_summary=f["reason"],
            tools_used=["run_agent_rule_check"],
            fix_hint="명시적인 도구 접근 정책·승인 요구·권한 검증을 확인하고 최소 권한을 적용하세요.",
        ).model_dump()
        for i, f in enumerate(profile.get("source_findings", []))
    ]
