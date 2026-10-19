"""정규화 JSON 증거 묶음 (기획서 5장). 검증은 스키마·참조 ID·크기·금지 필드만 담당하고 의미 해석은 하지 않는다."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.errors import AppError, ErrorCode

MAX_BUNDLE_BYTES = 1_000_000
# 결함 여부·등급·정답 경로·스캐너 출력은 입력에 넣을 수 없다 (기획서 5장).
FORBIDDEN_KEYS = frozenset(
    {
        "findings",
        "severity",
        "risk",
        "vulnerable",
        "expected",
        "ground_truth",
        "answer",
        "scanner_output",
        "attack_path",
    }
)

SideEffect = Literal["read", "write", "delete", "execute", "external_send"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Tool(_Model):
    id: str
    name: str
    description: str = ""
    args_schema: dict[str, Any] = Field(default_factory=dict)
    side_effect: SideEffect = "read"
    requires_approval: bool = False
    access_policy_ref: str | None = None  # 누가 호출할 수 있는지에 대한 정책 evidence_id
    chain_restricted: bool | None = None  # 다른 도구 결과를 입력으로 받는 연결이 정책으로 제한되는지 (None = 증거 없음)
    source_ref: str


class Principal(_Model):
    id: str
    kind: Literal["role", "user", "service"] = "role"
    trust: list[str] = Field(default_factory=list)  # 신뢰 관계: 이 주체를 맡을 수 있는 주체 ID
    source_ref: str


class Statement(_Model):
    effect: Literal["Allow", "Deny"]
    actions: list[str]
    resources: list[str]
    conditions: dict[str, Any] = Field(default_factory=dict)


class Policy(_Model):
    id: str
    principal_id: str
    statements: list[Statement]
    boundary: list[Statement] = Field(default_factory=list)  # 제공된 경계 정책만 기록
    source_ref: str


class ToolMapping(_Model):
    tool_id: str
    principal_id: str
    source_ref: str


class Mappings(_Model):
    tool_to_principal: list[ToolMapping] = Field(default_factory=list)


class IngressRule(_Model):
    cidr: str
    port: int | str


class Resource(_Model):
    id: str
    type: Literal["storage", "network", "database", "secret", "queue", "other"] = "other"
    public: bool | None = None
    sensitivity: Literal["low", "medium", "high"] | None = None
    encrypted: bool | None = None
    logging: bool | None = None
    ingress: list[IngressRule] = Field(default_factory=list)
    source_ref: str


class Source(_Model):
    id: str
    sha256: str | None = None
    location: str = ""
    evidence_ids: list[str] = Field(default_factory=list)


class BusinessPolicy(_Model):
    allowed_purposes: list[str] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)  # "<resource_id>: 사유" 형식의 정상 예외
    ticket_target: str | None = None  # 후속 조치 티켓의 허용 수신 대상 (D1 수신자 변경 판정에 사용)
    followup_allowed: bool = True


class MissingEvidence(_Model):
    id: str
    about: str  # 예: tool:T3 caller access policy
    note: str = ""


class EvidenceBundle(_Model):
    schema_version: str
    bundle_id: str
    tools: list[Tool] = Field(default_factory=list)
    principals: list[Principal] = Field(default_factory=list)
    policies: list[Policy] = Field(default_factory=list)
    mappings: Mappings = Field(default_factory=Mappings)
    resources: list[Resource] = Field(default_factory=list)
    business_policy: BusinessPolicy = Field(default_factory=BusinessPolicy)
    missing_evidence: list[MissingEvidence] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)

    def principal(self, pid: str) -> Principal | None:
        return next((p for p in self.principals if p.id == pid), None)

    def resource(self, rid: str) -> Resource | None:
        return next((r for r in self.resources if r.id == rid), None)

    def missing_about(self, about: str) -> MissingEvidence | None:
        return next((m for m in self.missing_evidence if m.about == about), None)


def _find_forbidden(obj: Any, path: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in FORBIDDEN_KEYS:
                found.append(f"{path}/{k}")
            found.extend(_find_forbidden(v, f"{path}/{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found.extend(_find_forbidden(v, f"{path}[{i}]"))
    return found


def _dupes(ids: list[str]) -> list[str]:
    return sorted({i for i in ids if ids.count(i) > 1})


def parse_bundle(raw: str | bytes | dict[str, Any]) -> EvidenceBundle:
    """스키마·참조 무결성·크기·금지 필드를 검증한다. 실패는 VALIDATION(details 에 사유 목록)."""
    if isinstance(raw, dict):
        data = raw
    else:
        if len(raw) > MAX_BUNDLE_BYTES:
            raise AppError(ErrorCode.VALIDATION, "evidence bundle too large", {"max_bytes": MAX_BUNDLE_BYTES})
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise AppError(ErrorCode.VALIDATION, f"invalid JSON: {e.msg}", {"line": e.lineno}) from e
    if not isinstance(data, dict):
        raise AppError(ErrorCode.VALIDATION, "evidence bundle must be a JSON object")
    forbidden = _find_forbidden(data)
    if forbidden:
        raise AppError(ErrorCode.VALIDATION, "bundle contains judgement fields", {"fields": forbidden})
    try:
        b = EvidenceBundle.model_validate(data)
    except ValidationError as e:
        errs = [{"loc": "/".join(map(str, x["loc"])), "msg": x["msg"]} for x in e.errors()[:20]]
        raise AppError(ErrorCode.VALIDATION, "bundle schema invalid", {"errors": errs}) from e

    problems: list[str] = []
    for name, ids in (
        ("tools", [t.id for t in b.tools]),
        ("principals", [p.id for p in b.principals]),
        ("policies", [p.id for p in b.policies]),
        ("resources", [r.id for r in b.resources]),
    ):
        problems += [f"duplicate {name} id: {d}" for d in _dupes(ids)]
    tool_ids = {t.id for t in b.tools}
    principal_ids = {p.id for p in b.principals}
    policy_ids = {p.id for p in b.policies}
    source_ids = {s.id for s in b.sources}
    for m in b.mappings.tool_to_principal:
        if m.tool_id not in tool_ids:
            problems.append(f"mapping references unknown tool: {m.tool_id}")
        if m.principal_id not in principal_ids:
            problems.append(f"mapping references unknown principal: {m.principal_id}")
    for p in b.policies:
        if p.principal_id not in principal_ids:
            problems.append(f"policy {p.id} references unknown principal: {p.principal_id}")
    for t in b.tools:
        if t.access_policy_ref and t.access_policy_ref not in policy_ids:
            problems.append(f"tool {t.id} references unknown access policy: {t.access_policy_ref}")
    if b.sources:  # sources 를 제공했다면 source_ref 가 거기에 있어야 한다
        refs: list[tuple[str, str]] = []
        for group in (b.tools, b.principals, b.policies, b.resources):
            refs += [(o.id, o.source_ref) for o in group]
        problems += [f"{oid} references unknown source: {ref}" for oid, ref in refs if ref not in source_ids]
    if problems:
        raise AppError(ErrorCode.VALIDATION, "bundle reference check failed", {"problems": problems[:30]})
    return b
