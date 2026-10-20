"""에이전트 감사 결정론 점검 A1~A4. 입력은 검증된 EvidenceBundle 의 도구·주체·정책 증거뿐이다.

원칙: 명시된 사실만 사용한다. 없는 연결을 만들지 않고, 판정에 필요한 증거가 없으면
위험을 확정하지 않고 판단 보류(HOLD)로 남긴다.
"""

from __future__ import annotations

import re
from fnmatch import fnmatchcase
from functools import lru_cache
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from core.agent_audit.bundle import EvidenceBundle, Policy, Resource, Tool
from core.settings import get_settings

Status = Literal["CONFIRMED", "CONDITIONAL", "HOLD"]
Severity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]


CATEGORIES: dict[str, list[str]] = {
    "A1": ["TOOL_NO_APPROVAL"],
    "A2": ["TOOL_ACCESS_POLICY_UNKNOWN"],
    "A3": ["TOOL_COMBINATION_RISK"],
    "A4": ["TOOL_DESCRIPTION_POISONING"],
}


class AgentFinding(BaseModel):
    area: Literal["A1", "A2", "A3", "A4"]
    category: str
    resource_id: str
    status: Status
    severity: Severity
    evidence_ids: list[str]
    reason: str
    conditions: list[str] = Field(default_factory=list)  # 위험이 성립하는 조건
    needs: list[str] = Field(default_factory=list)  # 판단 보류 시 추가로 필요한 증거
    fix_hint: str | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.area, self.category, self.resource_id)


class AgentReport(BaseModel):
    findings: list[AgentFinding]


@lru_cache
def load_agent_config() -> dict[str, Any]:
    path = get_settings().config_dir / "agent_checks.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _match(patterns: list[str], value: str) -> bool:
    return any(fnmatchcase(value.lower(), p.lower()) for p in patterns)


def _is_data_action(action: str) -> bool:
    if action in ("*", "*:*"):
        return True
    return action.split(":", 1)[0].lower() in {x.lower() for x in load_agent_config()["data_services"]}


def _reachable(pol: Policy, res: Resource) -> bool:
    """자원을 덮는 데이터 액션 Allow 가 있고 명시적 Deny 가 전부 가리지 않으면 도달 가능."""
    allows = [
        s
        for s in pol.statements
        if s.effect == "Allow" and _match(s.resources, res.id) and any(_is_data_action(a) for a in s.actions)
    ]
    denies = [s for s in pol.statements if s.effect == "Deny" and _match(s.resources, res.id) and not s.conditions]
    for a in allows:
        data_actions = [x for x in a.actions if _is_data_action(x)]
        if not all(any(_match(d.actions, act) for d in denies) for act in data_actions):
            return True
    return False


# ---------------- A1 / A2 / A4 (도구 정의) ----------------


@lru_cache
def _poison_patterns() -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p) for p in load_agent_config()["tool_poisoning"]["patterns"])


def check_tools(b: EvidenceBundle) -> list[AgentFinding]:
    out: list[AgentFinding] = []
    for t in b.tools:
        if t.side_effect in ("write", "delete", "execute", "external_send") and not t.requires_approval:
            out.append(
                AgentFinding(
                    area="A1",
                    category="TOOL_NO_APPROVAL",
                    resource_id=t.id,
                    status="CONFIRMED",
                    severity="CRITICAL" if t.side_effect == "delete" else "HIGH",
                    evidence_ids=[t.id, t.source_ref],
                    reason=f"{t.name}: side_effect={t.side_effect} without approval",
                    fix_hint="승인 요구를 켠다",
                )
            )
        if t.access_policy_ref is None and t.side_effect != "read":
            out.append(
                AgentFinding(
                    area="A2",
                    category="TOOL_ACCESS_POLICY_UNKNOWN",
                    resource_id=t.id,
                    status="HOLD",
                    severity="MEDIUM",
                    evidence_ids=[t.id, t.source_ref],
                    reason=f"{t.name}: 변경 도구의 호출 가능 주체를 확인할 정책 증거가 없음",
                    needs=[f"tool:{t.id} caller access policy"],
                )
            )
        hit = next((p.pattern for p in _poison_patterns() if p.search(t.description)), None)
        if hit:
            out.append(
                AgentFinding(
                    area="A4",
                    category="TOOL_DESCRIPTION_POISONING",
                    resource_id=t.id,
                    status="CONFIRMED",
                    severity="HIGH",
                    evidence_ids=[t.id, t.source_ref],
                    reason=f"{t.name}: description contains instruction-like text (pattern {hit!r})",
                    fix_hint="도구 설명에서 업무 외 지시를 제거한다",
                )
            )
    return out


# ---------------- A3 (도구 조합) ----------------


def check_combinations(b: EvidenceBundle) -> list[AgentFinding]:
    """A3: 민감 자원에 닿는 도구와 외부 전송 도구가 함께 있고 연결 제한 증거가 없으면 조건부 위험."""
    sensitive = [r for r in b.resources if r.sensitivity == "high"]
    tools = {t.id: t for t in b.tools}
    reach: list[Tool] = []
    for m in b.mappings.tool_to_principal:
        pols = [p for p in b.policies if p.principal_id == m.principal_id]
        if any(_reachable(p, res) for p in pols for res in sensitive):
            reach.append(tools[m.tool_id])
    out: list[AgentFinding] = []
    senders = [t for t in b.tools if t.side_effect == "external_send" and t.chain_restricted is not True]
    for src in reach:
        for snd in senders:
            if snd.id == src.id:
                continue
            out.append(
                AgentFinding(
                    area="A3",
                    category="TOOL_COMBINATION_RISK",
                    resource_id=snd.id,
                    status="CONDITIONAL",
                    severity="HIGH",
                    evidence_ids=[src.id, snd.id, src.source_ref, snd.source_ref],
                    reason=f"{src.name} 로 읽은 민감 데이터가 {snd.name} 로 외부 전송될 수 있음",
                    conditions=["같은 세션에서 두 도구가 연쇄 호출되고 연결 제한 정책이 없어야 성립"],
                    needs=[f"tool:{snd.id} chain restriction policy"],
                )
            )
    return out


def audit_agent(b: EvidenceBundle) -> AgentReport:
    findings = [*check_tools(b), *check_combinations(b)]
    seen: set[tuple[str, str, str]] = set()
    unique: list[AgentFinding] = []
    for f in findings:  # 같은 유형·도구의 반복 경고는 한 건
        if f.key not in seen:
            seen.add(f.key)
            unique.append(f)
    return AgentReport(findings=unique)
