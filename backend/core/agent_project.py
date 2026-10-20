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


def discover_agent(root: Path) -> dict[str, Any] | None:
    root = root.resolve()
    files = dict(iter_repo_files(root))
    probe_set = None
    if "audit-probes.yaml" in files:
        from core.probes import ProbeSet

        try:
            probe_set = ProbeSet.model_validate(yaml.safe_load(files["audit-probes.yaml"].read_text())).model_dump()
        except (ValueError, yaml.YAMLError) as exc:
            raise AppError(ErrorCode.VALIDATION, "audit-probes.yaml 공격 정의 형식 오류") from exc
    protocol = None
    if "audit-runtime.yaml" in files:
        try:
            settings = yaml.safe_load(files["audit-runtime.yaml"].read_text())
        except yaml.YAMLError as exc:
            raise AppError(ErrorCode.VALIDATION, "audit-runtime.yaml 형식 오류") from exc
        if not isinstance(settings, dict) or settings.get("protocol") not in {"atlas", "auditor"}:
            raise AppError(ErrorCode.VALIDATION, "protocol은 atlas 또는 auditor여야 합니다.")
        protocol = settings["protocol"]
    # Prefer explicit tool policy over source inference. Never follow runtime URLs from ZIP.
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
            "manifest": manifest | ({"_probe_set": probe_set} if probe_set else {}),
            "runtime_protocol": protocol or "auditor",
            "manifest_path": rel,
            "manifest_text": raw,
            "system_prompt": prompt or "",
            "prompt_path": prompt_rel,
            "source_findings": [],
            "discovery": "explicit_manifest",
        }
    tools: list[dict[str, Any]] = []
    prompts: list[str] = []
    findings: list[dict[str, Any]] = []
    tool_files: list[str] = []
    atlas = False
    for rel, path in files.items():
        if not rel.endswith(".py"):
            continue
        body = read_text(path) or ""
        if "/sessions" in body and "/chat" in body and "session_id" in body:
            atlas = True
        try:
            tree = ast.parse(body)
        except (SyntaxError, RecursionError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = {n.id for n in targets if isinstance(n, ast.Name)}
            value = node.value
            if value is None:
                continue
            if names & {"TOOL_SCHEMAS", "TOOLS", "tools"}:
                try:
                    literal = ast.literal_eval(value)
                except (ValueError, TypeError, SyntaxError, RecursionError):
                    continue
                if not isinstance(literal, list):
                    continue
                for item in literal:
                    if not isinstance(item, dict):
                        continue
                    function = item.get("function", item)
                    if not isinstance(function, dict) or not isinstance(function.get("name"), str):
                        continue
                    name = function["name"]
                    tools.append(
                        {
                            "name": name,
                            "description": str(function.get("description", "")),
                            "parameters": function.get("parameters", {}),
                            "policy_status": "UNKNOWN",
                            "source_path": rel,
                            "source_line": node.lineno,
                        }
                    )
                    tool_files.append(rel)
                    findings.append(
                        {
                            "category": "TOOL_ACCESS_POLICY_UNKNOWN",
                            "severity": "MEDIUM",
                            "path": rel,
                            "line": node.lineno,
                            "snippet": ast.get_source_segment(body, node) or "",
                            "title": f"도구 권한·승인 정책 확인 필요: {name}",
                            "reason": "소스에서 호출 가능한 도구 정의를 발견했으나 이 정의에는 접근 범위·승인 정책의 증거가 없습니다. 런타임 인증·인가 코드를 함께 검토해야 합니다. 취약점 확정이 아닌 검토 대상입니다.",
                        }
                    )
            if names & {"SYSTEM", "SYSTEM_PROMPT", "system_prompt"}:
                segment = ast.get_source_segment(body, value) or ""
                prompts.append(segment)
                if any(term in segment.upper() for term in ("SERVICE_TOKEN", "API_KEY", "PASSWORD", "SECRET")):
                    findings.append(
                        {
                            "category": "PROMPT_SECRET_EMBEDDED",
                            "severity": "MEDIUM",
                            "path": rel,
                            "line": node.lineno,
                            "snippet": segment,
                            "title": "시스템 프롬프트의 자격증명 참조",
                            "reason": "시스템 프롬프트 구성식이 자격증명 변수를 참조합니다. 실제 전송값과 변수 해석은 실행 환경 확인이 필요합니다.",
                        }
                    )
    if not tools:
        return None
    return {
        "kind": "source",
        "runtime_protocol": protocol or ("atlas" if atlas else "unknown"),
        "manifest": {"tools": tools, "policy_status": "UNKNOWN"} | ({"_probe_set": probe_set} if probe_set else {}),
        "manifest_path": tool_files[0],
        "manifest_text": yaml.safe_dump({"tools": tools}, allow_unicode=True),
        "system_prompt": "\n".join(prompts),
        "prompt_path": tool_files[0],
        "source_findings": findings,
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
