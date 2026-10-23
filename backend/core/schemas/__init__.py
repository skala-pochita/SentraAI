"""공용 Pydantic 스키마 (명세 8.2)."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from core.path_schema import AttackPath

Severity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]
SEVERITY_ORDER: dict[str, int] = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "INFO": 0}
Verdict = Literal["TRUE_POSITIVE", "FALSE_POSITIVE", "NEEDS_REVIEW"]


class Evidence(BaseModel):
    kind: Literal["code", "manifest", "probe", "standard"]
    file_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    snippet_masked: str | None = None  # 비밀값 마스킹된 코드 조각
    probe_id: str | None = None
    probe_transcript_ref: str | None = None  # probe_runs.id
    standard_ref: str | None = None  # "OWASP-LLM01", "CWE-798" 등
    label: str | None = None
    relation: str | None = None
    doc_chunk_id: str | None = None  # rag-mcp 청크 ID


class FindingDraft(BaseModel):
    temp_id: str
    target_id: str
    source: Literal["code", "agent", "correlation"]
    category: str
    rule_id: str | None = None
    title: str
    severity: Severity
    confidence: float = Field(ge=0, le=1)
    evidence: list[Evidence] = Field(default_factory=list)
    reasoning_summary: str = ""  # 사람이 읽는 2~3 문장 판단 근거
    tools_used: list[str] = Field(default_factory=list)  # 조사 노드가 호출한 도구 이름 (Agentic 증거)
    verdict: Verdict = "NEEDS_REVIEW"
    finding_id: str | None = None  # DB 저장 후 채움
    standard_refs: list[str] = Field(default_factory=list)
    fix_hint: str | None = None
    subject: str | None = None
    attack_path: AttackPath | None = None
    component_ids: list[str] = Field(default_factory=list)  # correlation: 구성 Finding temp_id

    @property
    def primary(self) -> Evidence | None:
        return self.evidence[0] if self.evidence else None


class ProbeResult(BaseModel):
    target_id: str | None = None
    probe_id: str
    category: str
    attempts: int
    successes: int
    asr: float
    judged_by: Literal["deterministic", "llm"]
    run_ids: list[str] = Field(default_factory=list)
    uncertain: int = 0
    adaptive: bool = False
    base_probe_id: str | None = None
    channel: str = "direct"
    tools_hit: list[str] = Field(default_factory=list)
    error: str | None = None


class FixProposal(BaseModel):
    proposal_id: str
    finding_ids: list[str]
    kind: Literal["code_patch", "manifest_patch", "prompt_patch"]
    diff: str
    patch_check_passed: bool
    branch: str | None = None
    pr_url: str | None = None
    target_id: str | None = None


class InvestigationResult(BaseModel):
    findings: list[FindingDraft]  # TP 확정 + 새로 발견한 것
    dismissed: list[dict[str, Any]] = Field(default_factory=list)  # [{temp_id, reason, rule_ref}] 오탐 처리
    tools_used: list[str] = Field(default_factory=list)


def normalize_snippet(snippet: str | None) -> str:
    return re.sub(r"\s+", " ", (snippet or "")).strip().lower()


def fingerprint(target_id: str, category: str, path: str | None, snippet: str | None) -> str:
    """fingerprint = hash(target, category, path, normalized_snippet) (명세 9.3 create_finding)."""
    blob = "|".join([target_id, category, path or "", normalize_snippet(snippet)])
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


def finding_fingerprint(f: FindingDraft) -> str:
    ev = f.primary
    if f.source == "correlation":
        return fingerprint(
            f.target_id, f.category, f.rule_id, ",".join(sorted(f.component_ids)) + "|" + (f.subject or "")
        )
    path = (ev.file_path or ev.probe_id) if ev else None
    snippet = ev.snippet_masked if ev and ev.snippet_masked else f.rule_id
    return fingerprint(f.target_id, f.category, path, snippet)
