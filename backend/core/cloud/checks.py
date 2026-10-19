"""C1~C3·A1·A4·B1 결정론 점검 (기획서 4장). 입력은 검증된 EvidenceBundle 뿐이다.

원칙: 명시된 사실만 사용한다. 없는 연결을 만들지 않고, 호출자 접근 정책처럼 판정에 필요한 증거가 없으면
위험을 확정하지 않고 판단 보류(HOLD)로 남긴다. 별표 권한만으로 권한 상승 성공을 확정하지 않는다.
"""

from __future__ import annotations

import re
from fnmatch import fnmatchcase
from functools import lru_cache
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from core.cloud.bundle import EvidenceBundle, Policy, Resource, Statement, Tool
from core.settings import get_settings

Status = Literal["CONFIRMED", "CONDITIONAL", "HOLD"]
Severity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]


class CloudFinding(BaseModel):
    area: Literal["A1", "A4", "B1", "C1", "C2", "C3"]
    category: str
    resource_id: str
    status: Status
    severity: Severity
    evidence_ids: list[str]
    reason: str
    conditions: list[str] = Field(default_factory=list)  # 위험이 성립하는 조건
    path: list[str] = Field(default_factory=list)  # B1: tool → principal → resource
    needs: list[str] = Field(default_factory=list)  # 판단 보류 시 추가로 필요한 증거
    fix_hint: str | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.area, self.category, self.resource_id)


class CloudReport(BaseModel):
    findings: list[CloudFinding]
    cleared: list[str]  # 점검했고 정상으로 판단한 항목 (예: "B1:T3->R2 blocked by Deny")
    not_checked: list[str]  # 입력 부족으로 점검하지 못한 항목


@lru_cache
def load_cloud_config() -> dict[str, Any]:
    path = get_settings().config_dir / "cloud_checks.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _match(patterns: list[str], value: str) -> bool:
    return any(fnmatchcase(value.lower(), p.lower()) for p in patterns)


def _covers(st: Statement, action: str, resource_id: str) -> bool:
    return _match(st.actions, action) and _match(st.resources, resource_id)


def _is_wildcard_action(a: str) -> bool:
    return a in load_cloud_config()["iam"]["wildcard_actions"]


# ---------------- C1 IAM ----------------


def check_iam(b: EvidenceBundle) -> list[CloudFinding]:
    cfg = load_cloud_config()["iam"]
    out: list[CloudFinding] = []
    for pol in b.policies:
        denied = [s for s in pol.statements if s.effect == "Deny"]
        for st in pol.statements:
            if st.effect != "Allow":
                continue
            wild_res = any(r in ("*", "arn:*") for r in st.resources)
            wild_act = [a for a in st.actions if _is_wildcard_action(a) or a in cfg["sensitive_service_wildcards"]]
            esc = [a for a in st.actions if _match(cfg["privilege_escalation_actions"], a)]
            has_boundary = bool(pol.boundary)
            if wild_act and wild_res:
                full = any(_is_wildcard_action(a) for a in wild_act)
                out.append(
                    CloudFinding(
                        area="C1",
                        category="IAM_OVERPRIVILEGED",
                        resource_id=pol.principal_id,
                        status="CONDITIONAL" if (st.conditions or has_boundary or denied) else "CONFIRMED",
                        severity="CRITICAL" if full else "HIGH",
                        evidence_ids=[pol.source_ref],
                        reason=f"{pol.id}: Allow {wild_act} on all resources",
                        conditions=_cond_notes(st, pol),
                        fix_hint="필요한 액션·리소스 ARN 으로 범위를 좁힌다",
                    )
                )
            elif esc and wild_res:
                out.append(
                    CloudFinding(
                        area="C1",
                        category="IAM_PRIVILEGE_ESCALATION_PATH",
                        resource_id=pol.principal_id,
                        status="CONDITIONAL",  # 별표만으로 상승 성공을 확정하지 않는다
                        severity="HIGH",
                        evidence_ids=[pol.source_ref],
                        reason=f"{pol.id}: {esc} allowed on all resources",
                        conditions=["대상 역할의 신뢰 정책이 호출 주체를 허용해야 성립", *_cond_notes(st, pol)],
                        fix_hint="PassRole/AssumeRole 대상 역할을 명시한다",
                    )
                )
    return out


def _cond_notes(st: Statement, pol: Policy) -> list[str]:
    notes = [f"조건 키 {k} 충족 시에만 적용" for k in st.conditions]
    if pol.boundary:
        notes.append("제공된 권한 경계가 허용 범위를 줄일 수 있음")
    if any(s.effect == "Deny" for s in pol.statements):
        notes.append("같은 정책의 Deny 문이 일부를 차단할 수 있음")
    return notes


# ---------------- C2 / C3 ----------------


def _is_public_cidr(cidr: str) -> bool:
    return cidr in load_cloud_config()["network"]["public_cidrs"]


def check_network(b: EvidenceBundle) -> list[CloudFinding]:
    allowed = {str(p) for p in load_cloud_config()["network"]["allowed_public_ports"]}
    out: list[CloudFinding] = []
    for r in b.resources:
        for rule in r.ingress:
            if not _is_public_cidr(rule.cidr) or str(rule.port) in allowed:
                continue
            allow_all = str(rule.port) in ("-1", "0-65535", "all", "*")
            out.append(
                CloudFinding(
                    area="C2",
                    category="NETWORK_PUBLIC_INGRESS",
                    resource_id=r.id,
                    status="CONFIRMED",
                    severity="CRITICAL" if allow_all else "HIGH",
                    evidence_ids=[r.source_ref],
                    reason=f"{r.id}: ingress {rule.cidr} port {rule.port}",
                    fix_hint="허용 CIDR 을 업무에 필요한 범위로 제한한다",
                )
            )
    return out


def check_storage(b: EvidenceBundle) -> list[CloudFinding]:
    out: list[CloudFinding] = []
    for r in b.resources:
        if r.type not in ("storage", "database", "secret"):
            continue
        if r.public is True:
            out.append(
                CloudFinding(
                    area="C3",
                    category="STORAGE_PUBLIC_ACCESS",
                    resource_id=r.id,
                    status="CONFIRMED",
                    severity="CRITICAL" if r.sensitivity == "high" else "HIGH",
                    evidence_ids=[r.source_ref],
                    reason=f"{r.id}: public access enabled",
                    fix_hint="공개 접근을 차단한다",
                )
            )
        if r.encrypted is False and r.sensitivity in ("medium", "high"):
            out.append(
                CloudFinding(
                    area="C3",
                    category="STORAGE_NOT_ENCRYPTED",
                    resource_id=r.id,
                    status="CONFIRMED",
                    severity="HIGH" if r.sensitivity == "high" else "MEDIUM",
                    evidence_ids=[r.source_ref],
                    reason=f"{r.id}: encryption disabled for {r.sensitivity} data",
                    fix_hint="저장 시 암호화를 켠다",
                )
            )
        if r.logging is False and r.sensitivity == "high":
            out.append(
                CloudFinding(
                    area="C3",
                    category="STORAGE_NO_ACCESS_LOGGING",
                    resource_id=r.id,
                    status="CONFIRMED",
                    severity="LOW",
                    evidence_ids=[r.source_ref],
                    reason=f"{r.id}: access logging disabled on sensitive data",
                )
            )
    return out


# ---------------- A1 / A4 ----------------


@lru_cache
def _poison_patterns() -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p) for p in load_cloud_config()["tool_poisoning"]["patterns"])


def check_tools(b: EvidenceBundle) -> list[CloudFinding]:
    out: list[CloudFinding] = []
    for t in b.tools:
        if t.side_effect in ("write", "delete", "execute", "external_send") and not t.requires_approval:
            out.append(
                CloudFinding(
                    area="A1",
                    category="TOOL_NO_APPROVAL",
                    resource_id=t.id,
                    status="CONFIRMED",
                    severity="CRITICAL" if t.side_effect == "delete" else "HIGH",
                    evidence_ids=[t.source_ref],
                    reason=f"{t.name}: side_effect={t.side_effect} without approval",
                    fix_hint="승인 요구를 켠다",
                )
            )
        hit = next((p.pattern for p in _poison_patterns() if p.search(t.description)), None)
        if hit:
            out.append(
                CloudFinding(
                    area="A4",
                    category="TOOL_DESCRIPTION_POISONING",
                    resource_id=t.id,
                    status="CONFIRMED",
                    severity="HIGH",
                    evidence_ids=[t.source_ref],
                    reason=f"{t.name}: description contains instruction-like text (pattern {hit!r})",
                    fix_hint="도구 설명에서 업무 외 지시를 제거한다",
                )
            )
    return out


# ---------------- B1 ----------------


def _reachable(pol: Policy, tool: Tool, res: Resource) -> tuple[bool, bool, list[str]]:
    """(허용됨, 조건부, 사유). 도구 부작용에 대응하는 액션 접두어로 판단하지 않고 정책의 Allow 가 자원을 덮는지만 본다."""
    allows = [s for s in pol.statements if s.effect == "Allow" and _match(s.resources, res.id)]
    if not allows:
        return False, False, []
    denies = [s for s in pol.statements if s.effect == "Deny" and _match(s.resources, res.id)]
    for a in allows:
        blocked = any(set(d.actions) >= set(a.actions) or _match(d.actions, "*") for d in denies if not d.conditions)
        if not blocked:
            conditional = bool(a.conditions) or bool(pol.boundary)
            return True, conditional, _cond_notes(a, pol)
    return False, False, ["explicit Deny"]


def build_paths(b: EvidenceBundle) -> tuple[list[CloudFinding], list[str], list[str]]:
    findings: list[CloudFinding] = []
    cleared: list[str] = []
    not_checked: list[str] = []
    tools = {t.id: t for t in b.tools}
    for m in b.mappings.tool_to_principal:
        tool = tools[m.tool_id]
        pols = [p for p in b.policies if p.principal_id == m.principal_id]
        if not pols:
            not_checked.append(f"B1:{tool.id}: principal {m.principal_id} 정책 증거 없음")
            continue
        for res in b.resources:
            if res.sensitivity != "high":
                continue
            reach = [(p, *_reachable(p, tool, res)) for p in pols]
            ok = [(p, cond, notes) for p, allowed, cond, notes in reach if allowed]
            if not ok:
                if any(n == ["explicit Deny"] for _, _, _, n in reach):
                    cleared.append(f"B1:{tool.id}->{res.id} blocked by explicit Deny")
                continue
            pol, cond, notes = ok[0]
            path = [tool.id, m.principal_id, res.id]
            evid = [tool.source_ref, m.source_ref, pol.source_ref, res.source_ref]
            caller_gap = tool.access_policy_ref is None
            if caller_gap:
                needs = [f"tool:{tool.id} caller access policy"]
                declared = b.missing_about(needs[0])
                findings.append(
                    CloudFinding(
                        area="B1",
                        category="TOOL_TO_SENSITIVE_RESOURCE_PATH",
                        resource_id=res.id,
                        status="HOLD",
                        severity="HIGH",
                        evidence_ids=evid + ([declared.id] if declared else []),
                        reason="도구→역할→민감 자원 연결은 있으나 호출 가능 주체를 알 수 없어 위험을 확정하지 않음",
                        path=path,
                        needs=needs,
                    )
                )
            else:
                findings.append(
                    CloudFinding(
                        area="B1",
                        category="TOOL_TO_SENSITIVE_RESOURCE_PATH",
                        resource_id=res.id,
                        status="CONDITIONAL" if cond else "CONFIRMED",
                        severity="CRITICAL" if tool.side_effect in ("delete", "external_send") else "HIGH",
                        evidence_ids=[*evid, tool.access_policy_ref or ""],
                        reason=f"{tool.name} 실행 역할 {m.principal_id} 이(가) 민감 자원 {res.id} 에 접근 가능",
                        conditions=notes,
                        path=path,
                        fix_hint="실행 역할의 자원 범위를 도구에 필요한 최소로 줄인다",
                    )
                )
    return findings, cleared, not_checked


def audit_bundle(b: EvidenceBundle) -> CloudReport:
    paths, cleared, not_checked = build_paths(b)
    findings = [*check_tools(b), *check_iam(b), *check_network(b), *check_storage(b), *paths]
    seen: set[tuple[str, str, str]] = set()
    unique: list[CloudFinding] = []
    for f in findings:  # 같은 유형·자원의 반복 경고는 한 건 (기획서 5장)
        if f.key not in seen:
            seen.add(f.key)
            unique.append(f)
    return CloudReport(findings=unique, cleared=cleared, not_checked=not_checked)
