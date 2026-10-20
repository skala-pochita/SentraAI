"""H3 도구 레지스트리·권한 게이트.

노드별 허용 도구 allowlist (조사 노드는 조회 도구만), 변경 도구 호출 시 역할·승인 확인, 노드당 호출 횟수 제한.
거부는 FORBIDDEN 으로 구조화되고 tool.denied 이벤트·로그로 남는다.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from core.errors import AppError, ErrorCode

READ_TOOLS = {
    "business": {
        "list_audit_targets",
        "list_repo_files",
        "read_repo_file",
        "search_repo",
        "get_agent_manifest",
        "get_findings",
        "get_audit_history",
    },
    "rag": {
        "search_security_standard",
        "search_framework_guide",
        "search_company_policy",
        "search_past_cases",
        "get_document",
        "list_documents",
        "list_false_positive_rules",
    },
}
EXEC_TOOLS = {"business": {"run_static_scan", "run_agent_rule_check", "run_injection_probe", "prepare_agent_sandbox", "stop_agent_sandbox"}}
MUTATING_TOOLS = {
    "business": {
        "create_finding",
        "update_finding_status",
        "request_approval",
        "create_fix_proposal",
        "apply_fix",
        "register_risk_acceptance",
        "save_report",
        "merge_fix",
    },
    "rag": {"ingest_document", "update_document", "delete_document", "register_false_positive"},
}

# 명세 8.3 노드 명세의 '사용 MCP 도구'
NODE_ALLOWLIST: dict[str, set[str]] = {
    "intake": {"list_audit_targets", "list_repo_files", "get_agent_manifest", "list_false_positive_rules"},
    "code_static_scan": {"run_static_scan", "list_false_positive_rules"},
    "code_investigate": {
        "read_repo_file",
        "search_repo",
        "list_repo_files",
        "search_security_standard",
        "search_framework_guide",
        "search_company_policy",
        "list_false_positive_rules",
        "search_past_cases",
    },
    "code_evidence_check": {"read_repo_file"},
    "agent_static_check": {"run_agent_rule_check", "get_agent_manifest"},
    "prepare_sandbox": {"prepare_agent_sandbox"},
    "probe_generate": {"search_security_standard", "get_agent_manifest"},
    "probe_run": {"run_injection_probe"},
    "probe_judge": set(),
    "correlate": {"create_finding", "get_findings"},
    "human_approval": {"request_approval", "update_finding_status"},
    "risk_accept": {"register_risk_acceptance", "update_finding_status"},
    "fix_generate": {"read_repo_file", "create_fix_proposal", "search_framework_guide", "get_agent_manifest"},
    "apply_fix": {"apply_fix", "update_finding_status"},
    "wait_merge": set(),
    "re_verify": {
        "run_static_scan",
        "run_injection_probe",
        "update_finding_status",
        "run_agent_rule_check",
        "get_findings",
    },
    "mark_manual": {"update_finding_status"},
    "report": {"save_report", "get_findings", "stop_agent_sandbox"},
    "eval_single": {"list_repo_files", "read_repo_file", "get_agent_manifest"},
    # API 계층(사용자 요청)에서 호출하는 도구 — 역할·소유 검사는 API 가 먼저 한다
    "api": {
        "merge_fix",
        "register_false_positive",
        "update_finding_status",
        "ingest_document",
        "update_document",
        "delete_document",
        "list_documents",
        "get_document",
        "list_false_positive_rules",
    },
}

DEFAULT_MAX_CALLS = 60
DEVELOPER_MUTATING = {"register_false_positive", "update_finding_status", "create_finding", "save_report"}


SERVER_TOOLS: dict[str, set[str]] = {
    "business": READ_TOOLS["business"] | EXEC_TOOLS["business"] | MUTATING_TOOLS["business"],
    "rag": READ_TOOLS["rag"] | MUTATING_TOOLS["rag"],
}


def server_of(tool: str) -> str:
    for server, names in SERVER_TOOLS.items():
        if tool in names:
            return server
    raise AppError(ErrorCode.VALIDATION, f"unknown tool {tool}")


def is_mutating(tool: str) -> bool:
    return any(tool in names for names in MUTATING_TOOLS.values())


def read_only_tools_for(node: str) -> list[str]:
    allowed = NODE_ALLOWLIST.get(node, set())
    return sorted(t for t in allowed if not is_mutating(t) and t not in EXEC_TOOLS["business"])


@dataclass
class ToolGate:
    max_calls_per_node: int = DEFAULT_MAX_CALLS
    counts: Counter[str] = field(default_factory=Counter)
    denied: list[dict[str, str]] = field(default_factory=list)

    def check(self, node: str, tool: str, actor_role: str) -> None:
        if tool in {"request_approval", "create_fix_proposal", "apply_fix", "merge_fix", "register_risk_acceptance"}:
            raise AppError(ErrorCode.FORBIDDEN, "수정·승인·병합 기능은 제공하지 않습니다.")
        allowed = NODE_ALLOWLIST.get(node)
        if allowed is None or tool not in allowed:
            self._deny(node, tool, "tool not allowed for node")
        if is_mutating(tool) and actor_role != "ADMIN" and tool not in DEVELOPER_MUTATING:
            self._deny(node, tool, f"role {actor_role} cannot call mutating tool")
        self.counts[node] += 1
        if self.counts[node] > self.max_calls_per_node:
            self._deny(node, tool, "tool call limit exceeded")

    def _deny(self, node: str, tool: str, reason: str) -> None:
        self.denied.append({"node": node, "tool": tool, "reason": reason})
        raise AppError(ErrorCode.FORBIDDEN, f"H3 tool gate: {reason}", {"node": node, "tool": tool})


# 같은 입력 재호출이 같은 결과인 변경 도구 (fingerprint·idempotency_key·upsert)
IDEMPOTENT_MUTATING = {
    "create_finding",
    "request_approval",
    "apply_fix",
    "merge_fix",
    "save_report",
    "update_finding_status",
    "register_risk_acceptance",
    "create_fix_proposal",
    "ingest_document",
    "update_document",
}


def tool_annotations(tool: str) -> dict[str, bool]:
    """MCP ToolAnnotations: 조회 readOnlyHint, 변경 destructiveHint (강의 표 6-13 '승인')."""
    if is_mutating(tool):
        return {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": tool in IDEMPOTENT_MUTATING,
            "openWorldHint": False,
        }
    if tool in EXEC_TOOLS["business"]:
        # 실행 도구: 감사 대상은 바꾸지 않지만 probe 는 샌드박스 에이전트(외부)를 호출한다
        return {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": tool != "run_injection_probe",
            "openWorldHint": tool == "run_injection_probe",
        }
    return {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
