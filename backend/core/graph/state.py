"""AuditState (명세 8.2).

체크포인트 직렬화 안정성을 위해 State 에는 Pydantic 모델 대신 model_dump() 된 dict 를 저장한다
(FindingDraft·ProbeResult·FixProposal 스키마는 core.schemas, DECISIONS #7).
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

FindingD = dict[str, Any]  # FindingDraft.model_dump()
ProbeResultD = dict[str, Any]  # ProbeResult.model_dump()
FixProposalD = dict[str, Any]  # FixProposal.model_dump()


class AuditState(TypedDict, total=False):
    # 식별
    audit_id: str
    thread_id: str
    requested_by: str  # user_id
    actor_role: str  # ADMIN | DEVELOPER | AUDITOR
    eval_config: str | None  # 비교 실험 시 "B_nano_harness" 등, 일반 감사는 None
    mode: str  # full | code | agent
    compliance_profile: dict[str, Any]
    # 대상
    targets: list[dict[str, Any]]  # [{target_id, type: repo|agent, ref, name, linked_target_id}]
    plan: dict[str, Any]  # intake 가 정한 검사 범위, 위험 파일 후보
    # 결과 (병렬 서브그래프 병합 → reducer)
    code_findings: Annotated[list[FindingD], operator.add]
    agent_findings: Annotated[list[FindingD], operator.add]
    probe_results: Annotated[list[ProbeResultD], operator.add]
    probe_round: int
    correlated: list[FindingD]  # 결합 위험 포함 최종 목록
    agent_coverage: Annotated[list[dict[str, Any]], operator.add]
    risk_paths: list[dict[str, Any]]
    correlation_gaps: list[dict[str, str]]
    # 승인·수정
    needs_approval: bool
    approval: dict[str, Any] | None  # {approval_id, decision, decided_by, comment, decided_at}
    accepted_risks: list[str]  # finding_ids
    fix_targets: list[str]  # 수정 대상 finding_ids
    fix_proposals: list[FixProposalD]
    fix_feedback: list[str]  # 재시도 시 이전 실패 사유
    merge_status: Literal["PENDING", "MERGED", "SKIPPED"] | None
    retry_count: int
    verify_result: dict[str, Any] | None  # {resolved: [ids], unresolved: [ids], new: [ids]}
    # 산출
    report_id: str | None
    # 관측
    errors: Annotated[list[dict[str, Any]], operator.add]
    token_usage: Annotated[list[dict[str, Any]], operator.add]  # {node, model, in, out, usd}


class CodeAuditState(TypedDict, total=False):
    """code_audit 서브그래프 내부 상태."""

    audit_id: str
    eval_config: str | None
    actor_role: str
    requested_by: str
    target: dict[str, Any]
    plan: dict[str, Any]
    candidates: list[FindingD]  # 정적 후보 (verdict=NEEDS_REVIEW)
    investigated: list[FindingD]
    code_findings: list[FindingD]
    errors: Annotated[list[dict[str, Any]], operator.add]
    token_usage: Annotated[list[dict[str, Any]], operator.add]


class CodeAuditOutput(TypedDict, total=False):
    code_findings: list[FindingD]
    errors: list[dict[str, Any]]
    token_usage: list[dict[str, Any]]


class AgentAuditState(TypedDict, total=False):
    """agent_audit 서브그래프 내부 상태."""

    audit_id: str
    eval_config: str | None
    actor_role: str
    requested_by: str
    target: dict[str, Any]
    plan: dict[str, Any]
    runtime: dict[str, Any]
    agent_coverage: list[dict[str, Any]]
    manifest: dict[str, Any]
    agent_findings: list[FindingD]
    probes: list[dict[str, Any]]  # 이번 라운드에 실행할 probe [{probe_id, payload?, kb_docs?, adaptive, base}]
    probe_results: list[ProbeResultD]
    probe_round: int
    uncertain_probe_ids: list[str]
    errors: Annotated[list[dict[str, Any]], operator.add]
    token_usage: Annotated[list[dict[str, Any]], operator.add]


class AgentAuditOutput(TypedDict, total=False):
    agent_coverage: list[dict[str, Any]]
    agent_findings: list[FindingD]
    probe_results: list[ProbeResultD]
    probe_round: int
    errors: list[dict[str, Any]]
    token_usage: list[dict[str, Any]]
