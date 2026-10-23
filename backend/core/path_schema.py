"""Versioned, evidence-backed attack path contract shared by graph, storage and API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

EvidenceLevel = Literal["EXECUTION", "STATIC", "CONDITIONAL"]
LEVEL_LABELS = {"EXECUTION": "실행 확인", "STATIC": "정적 근거", "CONDITIONAL": "조건부 추정"}


class PathRef(BaseModel):
    evidence_index: int | None = Field(default=None, ge=0)
    finding_id: str | None = None
    target_id: str
    probe_run_id: str | None = None
    tool_call_index: int | None = Field(default=None, ge=0)
    file_path: str | None = None
    line_start: int | None = Field(default=None, ge=1)
    field_path: str | None = None
    value_masked: str | None = None

    @model_validator(mode="after")
    def check_ref(self) -> PathRef:
        if not (self.finding_id or self.probe_run_id):
            raise ValueError("reference requires a finding or probe run")
        if self.evidence_index is not None and not self.finding_id:
            raise ValueError("evidence index requires a finding")
        if self.tool_call_index is not None and not self.probe_run_id:
            raise ValueError("tool call requires a probe run")
        return self


class PathItem(BaseModel):
    evidence_refs: list[PathRef] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)


class PathStep(PathItem):
    id: str
    kind: Literal["input", "execution", "control", "code", "impact"]
    title: str
    claim: str
    evidence_level: EvidenceLevel
    target_id: str


class PathEdge(PathItem):
    id: str
    from_step_id: str
    to_step_id: str
    relation: str
    evidence_level: EvidenceLevel


class CodeReason(BaseModel):
    kind: Literal["policy", "implementation_candidate", "code_finding"]
    title: str
    explanation: str
    ref: PathRef
    snippet_masked: str

    @model_validator(mode="after")
    def require_snapshot_ref(self) -> CodeReason:
        if self.ref.evidence_index is None or not self.ref.file_path or self.ref.line_start is None:
            raise ValueError("code reason requires an indexed source snapshot")
        return self


class AttackPath(BaseModel):
    schema_version: Literal[1] = 1
    target_ids: list[str]
    steps: list[PathStep]
    edges: list[PathEdge]
    severity_reason: str | None = None
    code_reasons: list[CodeReason] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    runtime_mode: Literal["mock", "live", "unknown"] = "unknown"
    observation_source: str = "static_analysis"

    @model_validator(mode="after")
    def check_graph(self) -> AttackPath:
        ids = {s.id for s in self.steps}
        if len(ids) != len(self.steps) or not ids:
            raise ValueError("step IDs must be unique and nonempty")
        if len({e.id for e in self.edges}) != len(self.edges):
            raise ValueError("edge IDs must be unique")
        for step in self.steps:
            if step.target_id not in self.target_ids:
                raise ValueError("step target outside path")
            if step.evidence_level != "CONDITIONAL" and not step.evidence_refs:
                raise ValueError("confirmed step requires evidence")
        for edge in self.edges:
            if edge.from_step_id not in ids or edge.to_step_id not in ids:
                raise ValueError("edge references unknown step")
            if edge.from_step_id == edge.to_step_id:
                raise ValueError("self edge is not an attack path")
            if edge.evidence_level != "CONDITIONAL" and not edge.evidence_refs:
                raise ValueError("confirmed edge requires evidence")
        if any(reason.ref.target_id not in self.target_ids for reason in self.code_reasons):
            raise ValueError("code reason target outside path")
        for item in [*self.steps, *self.edges]:
            if any(r.target_id not in self.target_ids for r in item.evidence_refs):
                raise ValueError("reference target outside path")
        return self
