"""Deterministic, original-backed review recommendations. Never a legal decision."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.errors import AppError, ErrorCode
from core.regulation_sources import HASH, Clause, canonical_hash, compact, evidence_candidate, load_clauses, sha256
from core.settings import get_settings
from harness.masking import mask_obj, mask_text

BANNER = (
    "이 섹션은 규제·가이드라인 관련 검토 대상을 알리는 권고입니다. 적용 여부는 사용자가 판단합니다. "
    "원문 인용은 공백을 제외하고 기계적으로 대조했으며, Finding과의 관련성은 별도로 표시합니다."
)
STATES = {
    "REVIEW_RECOMMENDED": "검토 권고",
    "NO_RELATED_FINDING": "관련 미해결 Finding 없음",
    "NOT_ASSESSED": "미점검",
    "OUT_OF_SCOPE": "점검 범위 밖",
    # Stored v1 reports remain readable; they are never recomputed with today's catalog.
    "ISSUES_OPEN": "미해결 이슈 (기존 형식)",
    "EVIDENCE_NO_ISSUE": "관련 미해결 Finding 없음 (기존 형식)",
}
RELEVANCE = {
    "ai_estimated": "AI 추정",
    "human_confirmed": "확인됨",
    "rejected": "기각",
    "review_required": "원문 변경·재검토 필요",
}
APPLICABILITY = {
    "REQUIRES_REVIEW": "적용 여부 확인 필요",
    "REFERENCE_ONLY": "참고 기준",
    "REPORTED_OUT_OF_SCOPE": "사용자 입력상 비해당·최종 적용 여부 확인 필요",
    "conditional": "적용 조건 확인 필요",
}
DEFER_REASONS = {
    "NO_RELEVANT_EVIDENCE": "검토된 관련 근거 연결 없음",
    "EVIDENCE_NOT_ELIGIBLE": "목차·예시 또는 미분류 원문이므로 자동 연결 보류",
    "HUMAN_REJECTED": "검토자가 기각한 연결",
    "SOURCE_MISMATCH": "원문·인용 검증 실패로 연결 보류",
    "EVALUATION_FAILED": "관련성 평가 실패로 연결 보류",
    "SOURCE_CHANGED": "원문 변경으로 기존 검토 상태 재확인 필요",
    "SOURCE_REDACTED": "민감정보 마스킹으로 인용이 변경되어 연결 보류",
    "RELEVANCE_REVIEW_REQUIRED": "문제와 조항의 관련성 또는 적용 전제 추가 확인 필요",
}


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sector: Literal["finance", "general"] | None = None
    high_impact_ai: Literal["YES", "NO", "UNDETERMINED"] = "UNDETERMINED"


class Link(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: dict[str, str]
    clause: str
    quote: str = Field(min_length=1)
    quote_sha256: str = Field(pattern=HASH)
    source_sha256: str = Field(pattern=HASH)
    relevance: Literal["ai_estimated", "human_confirmed", "rejected", "review_required"] = "ai_estimated"
    reason: str = Field(min_length=1)
    generated_by: dict[str, Any]
    confirmed_by: str | None = None
    confirmed_at: str | None = None

    @model_validator(mode="after")
    def valid(self) -> Link:
        if len(self.key) != 1 or not set(self.key) <= {"rule_id", "category", "standard_ref", "audit"}:
            raise ValueError("link key must contain one supported matcher")
        if not next(iter(self.key.values())).strip():
            raise ValueError("empty matcher")
        if sha256(self.quote) != self.quote_sha256 or not compact(self.quote):
            raise ValueError("quote hash mismatch or empty quote")
        if self.relevance in {"human_confirmed", "rejected"} and not (self.confirmed_by and self.confirmed_at):
            raise ValueError("human decisions require reviewer and timestamp")
        return self

    @property
    def identity(self) -> str:
        return canonical_hash([self.key, self.clause])


class Catalog(BaseModel):
    version: str
    digest: str
    clauses: dict[str, Clause]
    links: list[Link]
    missing_sources: list[dict[str, str]]
    generation: dict[str, Any] = Field(default_factory=dict)
    review_queue: list[Link] = Field(default_factory=list)


def validate_links(links: list[Link], clauses: dict[str, Clause]) -> None:
    seen = set()
    for link in links:
        clause = clauses.get(link.clause)
        if (
            clause is None
            or link.source_sha256 != clause.source_sha256
            or compact(link.quote) not in compact(clause.text)
        ):
            raise AppError(ErrorCode.VALIDATION, f"link quote/provenance mismatch: {link.clause}")
        if link.identity in seen:
            raise AppError(ErrorCode.VALIDATION, "duplicate compliance link")
        seen.add(link.identity)


def load_catalog(path: Path | None = None, source_root: Path | None = None) -> Catalog:
    path = path or (get_settings().compliance_config_dir or get_settings().config_dir / "compliance")
    try:
        config = yaml.safe_load((path / "catalog.yaml").read_text(encoding="utf-8"))
        clauses, missing = load_clauses(source_root)
        links: list[Link] = []
        for file in sorted((path / "links").glob("*.yaml")):
            links.extend(Link.model_validate(x) for x in (yaml.safe_load(file.read_text(encoding="utf-8")) or []))
        review_queue = [
            link
            for link in links
            if link.clause not in clauses or link.source_sha256 != clauses[link.clause].source_sha256
        ]
        links = [link for link in links if link not in review_queue]
        validate_links(links, clauses)
        generation_file = path / "last_generation.local.yaml"
        generation = yaml.safe_load(generation_file.read_text(encoding="utf-8")) if generation_file.exists() else {}
        digest = canonical_hash(
            {
                "config": config,
                "clauses": {k: c.model_dump() for k, c in clauses.items()},
                "links": [link.model_dump() for link in links],
                "missing": missing,
                "generation": generation,
                "review_queue": [link.model_dump() for link in review_queue],
            }
        )
        return Catalog(
            version=config["version"],
            digest=digest,
            clauses=clauses,
            links=links,
            missing_sources=missing,
            generation=generation or {},
            review_queue=review_queue,
        )
    except (ValueError, KeyError, OSError, yaml.YAMLError) as exc:
        raise AppError(ErrorCode.VALIDATION, "invalid compliance catalog", {"type": type(exc).__name__}) from exc


def links_for(finding: dict[str, Any], catalog: Catalog) -> list[Link]:
    for field in ("rule_id", "category", "standard_ref"):
        values = finding.get("standard_refs", []) if field == "standard_ref" else [finding.get(field)]
        matches = [link for link in catalog.links if link.key.get(field) in values and field in link.key]
        if any(link.key.get(field) in values and field in link.key for link in catalog.review_queue):
            return []  # A stale precise decision cannot be bypassed by broad regeneration.
        if matches:
            # A rejection at a precise tier blocks fallback to a broader tier.
            eligible = [
                link
                for link in matches
                if link.relevance in {"ai_estimated", "human_confirmed"}
                and evidence_candidate(catalog.clauses[link.clause])
            ]
            confirmed = [link for link in eligible if link.relevance == "human_confirmed"]
            return confirmed or eligible
    return []


def _condition(condition: str, profile: Profile) -> str:
    if condition == "finance_customer_impact":
        return "금융 업무에서 고객 영향도가 큰 의사결정을 하는 경우인지 추가 확인"
    if condition == "finance_high_risk":
        return "금융 업무의 고영향·고위험 AI인지 추가 확인; 단발 검사로 정기 이행을 확인할 수 없음"
    if condition == "finance":
        return "금융서비스 제공 업무에 쓰이는 AI 시스템인 경우" + (
            " (일반 업종 선택: 참고 자료)" if profile.sector == "general" else ""
        )
    if condition == "high_impact_ai":
        return "고영향 AI에 해당하는 경우" + (" (사용자 입력: 비해당)" if profile.high_impact_ai == "NO" else "")
    if condition == "generative_or_high_impact":
        return "생성형 또는 고영향 AI 제품·서비스 제공 여부를 확인해야 합니다."
    return "문서가 정한 대상과 적용 조건을 사용자 확인"


def _applicability(clause: Clause, profile: Profile) -> str:
    if clause.condition in {"finance", "finance_customer_impact", "finance_high_risk"} and profile.sector == "general":
        return "REFERENCE_ONLY"
    if clause.condition == "high_impact_ai" and profile.high_impact_ai == "NO":
        return "REPORTED_OUT_OF_SCOPE"
    # Self-reported sector/high-impact status never establishes legal applicability.
    return "REQUIRES_REVIEW"


def _review_reason(link: Link) -> str:
    if link.generated_by.get("assistant_review", {}).get("action") in {"hold", "superseded"}:
        return "RELEVANCE_REVIEW_REQUIRED"
    return "SOURCE_CHANGED"


def _state(clause: Clause, findings: list[dict[str, Any]], report: dict[str, Any], links: list[Link]) -> str:
    if not clause.assessable or clause.evidence_scope == "organization":
        return "OUT_OF_SCOPE"
    coverage = report.get("agent_coverage", [])
    # Any mocked agent run must not be presented as measured security evidence.
    if any(c.get("model_verification") == "MOCK" or c.get("runtime", {}).get("mode") == "mock" for c in coverage):
        return "NOT_ASSESSED"
    active = [
        f
        for f in findings
        if f.get("status", "OPEN") in {"OPEN", "FIXING", "MANUAL_REQUIRED", "ACCEPTED_RISK"}
        and f.get("verdict") != "FALSE_POSITIVE"
    ]
    if any(str(f.get("rule_id") or "").startswith("PROBE-") for f in active) and not (
        coverage and all(c.get("model_verification") == "LIVE_VERIFIED" for c in coverage)
    ):
        return "NOT_ASSESSED"
    if active:
        return "REVIEW_RECOMMENDED"
    audit_keys = {link.key.get("audit") for link in links}
    if audit_keys & {"audit_execution", "report_document"} and report.get("targets"):
        return "NO_RELATED_FINDING"
    categories = {link.key["category"] for link in links if "category" in link.key}
    relevant = [p for p in report.get("probes", []) if p.get("category") in categories]
    if "probe_execution" in audit_keys:
        relevant = [p for p in report.get("probes", []) if p.get("category") != "BENIGN_CONTROL"]
    # Successes without a corresponding Finding, partial errors, or no positive control are not clean evidence.
    controls = [p for p in report.get("probes", []) if p.get("category") == "BENIGN_CONTROL"]
    valid_runs = relevant + controls
    live = bool(coverage) and all(
        c.get("model_verification") == "LIVE_VERIFIED" and c.get("attacks") == "COMPLETED" for c in coverage
    )
    if (
        live
        and relevant
        and controls
        and all(
            p.get("attempts", 0)
            and not p.get("error")
            and str(p.get("judged_by", "deterministic")).lower() in {"deterministic", "llm"}
            for p in valid_runs
        )
        and all(p.get("successes") == p.get("attempts") for p in controls)
        and all(p.get("successes") == 0 for p in relevant)
    ):
        return "NO_RELATED_FINDING"
    return "NOT_ASSESSED"


def build_compliance(report: dict[str, Any], catalog: Catalog, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    p = Profile.model_validate(profile or {})
    matched: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_clause: dict[str, list[Link]] = defaultdict(list)
    unmapped = []
    decisions = []
    withheld = []
    for link in catalog.links:
        if link.relevance in {"ai_estimated", "human_confirmed"} and evidence_candidate(catalog.clauses[link.clause]):
            by_clause[link.clause].append(link)
        elif link.relevance != "rejected":
            withheld.append(
                {
                    "link_id": link.identity,
                    "clause": link.clause,
                    "reason": _review_reason(link) if link.relevance == "review_required" else "EVIDENCE_NOT_ELIGIBLE",
                }
            )
    for finding in report.get("findings", []):
        hits = links_for(finding, catalog)
        finding["compliance_refs"] = sorted({link.clause for link in hits})
        if not hits:
            unmapped.append(finding.get("finding_id"))
        matching = []
        for field in ("rule_id", "category", "standard_ref"):
            values = finding.get("standard_refs", []) if field == "standard_ref" else [finding.get(field)]
            matching = [link for link in catalog.links if field in link.key and link.key[field] in values]
            stale_matches = [link for link in catalog.review_queue if field in link.key and link.key[field] in values]
            if matching or stale_matches:
                break
        reason = (
            "SOURCE_CHANGED"
            if stale_matches
            else next(
                (_review_reason(link) for link in matching if link.relevance == "review_required"), "SOURCE_CHANGED"
            )
            if any(link.relevance == "review_required" for link in matching)
            else "HUMAN_REJECTED"
            if matching and all(link.relevance == "rejected" for link in matching)
            else "EVIDENCE_NOT_ELIGIBLE"
            if matching
            else "NO_RELEVANT_EVIDENCE"
        )
        if not matching and any(
            d.get("reason") == "EVALUATION_FAILED"
            and d.get("rule") in {finding.get("rule_id"), finding.get("category")}
            for d in catalog.generation.get("deferred", [])
        ):
            reason = "EVALUATION_FAILED"
        decision = {
            "finding_id": finding.get("finding_id"),
            "status": "HUMAN_CONFIRMED"
            if hits and all(link.relevance == "human_confirmed" for link in hits)
            else "CANDIDATE"
            if hits
            else "DEFERRED",
            "reason": None if hits else reason,
            "clause_ids": finding["compliance_refs"],
        }
        finding["compliance_mapping"] = decision
        decisions.append(decision)
        for cid in finding["compliance_refs"]:
            matched[cid].append(dict(finding))
    recommendations = []
    for cid, links in sorted(by_clause.items()):
        clause = catalog.clauses[cid]
        evidence = matched[cid]
        recommendations.append(
            {
                "control_id": cid,
                "source": clause.source,
                "title": clause.title,
                "heading": clause.heading,
                "issuer": clause.issuer,
                "source_url": clause.source_url,
                "version": clause.version,
                "page_start": clause.page_start,
                "page_end": clause.page_end,
                "source_sha256": clause.source_sha256,
                "file_sha256": clause.file_sha256,
                "provenance": "등록된 로컬 원문과 대조; 공식본 동일성은 별도 확인",
                "condition": _condition(clause.condition, p),
                "applicability": _applicability(clause, p),
                "content_kind": clause.content_kind,
                "evidence_scope": clause.evidence_scope,
                "parent_id": clause.parent_id,
                "additional_review": (
                    "조직 절차와 운영 증적을 별도로 확인해야 합니다."
                    if clause.evidence_scope == "organization"
                    else "실제 통제 구현·작동 여부와 적용 조건을 확인해야 합니다."
                ),
                "status": _state(clause, evidence, report, links),
                "findings": evidence,
                "probes": [
                    probe
                    for probe in report.get("probes", [])
                    if any(link.key.get("category") == probe.get("category") for link in links)
                ],
                "evidence": [
                    {
                        **link.model_dump(),
                        "link_id": link.identity,
                        "quote_verified": True,
                        "via": next(iter(link.key)) + ":" + next(iter(link.key.values())),
                    }
                    for link in links
                ],
            }
        )
    return {
        "schema": 2,
        "banner": BANNER,
        "catalog_version": catalog.version,
        "catalog_digest": "sha256:" + catalog.digest,
        "profile": p.model_dump(),
        "status": "AVAILABLE" if recommendations else "NOT_ASSESSED",
        "recommendations": recommendations,
        "unmapped_findings": unmapped,
        "mapping_decisions": decisions,
        "withheld_links": withheld,
        "review_required_links": [
            {"link_id": link.identity, "clause": link.clause, "reason": "SOURCE_CHANGED"}
            for link in catalog.review_queue
        ],
        "missing_sources": catalog.missing_sources,
        "limitations": [
            "원본 코드 미변경",
            "인용 일치는 관련성이나 법적 적용 여부를 보증하지 않습니다.",
            "미해결 Finding이 없어도 조직 절차·주기적 운영 이행은 확인하지 못합니다.",
            "실행하지 못한 검사·모의 결과는 미점검으로 표시합니다.",
        ],
    }


def build_compliance_safely(report: dict[str, Any], profile: dict[str, Any] | None = None) -> dict[str, Any]:
    """A broken regulation catalog cannot erase an otherwise valid security report."""
    try:
        return build_compliance(report, load_catalog(), profile)
    except AppError:
        decisions = []
        for finding in report.get("findings", []):
            finding["compliance_refs"] = []
            item = {
                "finding_id": finding.get("finding_id"),
                "status": "DEFERRED",
                "reason": "SOURCE_MISMATCH",
                "clause_ids": [],
            }
            finding["compliance_mapping"] = item
            decisions.append(item)
        return {
            "schema": 2,
            "banner": BANNER,
            "status": "NOT_ASSESSED",
            "recommendations": [],
            "mapping_decisions": decisions,
            "unmapped_findings": [d["finding_id"] for d in decisions],
            "missing_sources": [],
            "limitations": ["원문·매핑 자료 검증 실패. 보안 검사 결과는 유지하고 근거 연결을 보류했습니다."],
        }


def seal_report(report: dict[str, Any]) -> dict[str, Any]:
    """Mask first, then hash the exact stored JSON excluding only its own seal."""
    clean = cast(dict[str, Any], mask_obj(report))
    section = clean.get("compliance")
    if section:
        # Redaction must never leave a changed quotation labelled as an exact match.
        original_items = {r["control_id"]: r for r in report.get("compliance", {}).get("recommendations", [])}
        retained = []
        for item in section.get("recommendations", []):
            original = {ev["link_id"]: ev["quote"] for ev in original_items[item["control_id"]]["evidence"]}
            item["evidence"] = [ev for ev in item["evidence"] if ev["quote"] == original.get(ev["link_id"])]
            if item["evidence"]:
                retained.append(item)
        if section.get("schema") == 2:
            section["recommendations"] = retained
            retained_ids = {item["control_id"] for item in retained}
            for finding in clean.get("findings", []):
                refs = finding.get("compliance_refs", [])
                kept = [cid for cid in refs if cid in retained_ids]
                if kept == refs:
                    continue
                finding["compliance_refs"] = kept
                decision = finding.get("compliance_mapping", {})
                decision["clause_ids"] = kept
                if not kept:
                    decision.update(status="DEFERRED", reason="SOURCE_REDACTED")
                    if finding.get("finding_id") not in section.get("unmapped_findings", []):
                        section.setdefault("unmapped_findings", []).append(finding.get("finding_id"))
                finding["compliance_mapping"] = decision
                for stored in section.get("mapping_decisions", []):
                    if stored.get("finding_id") == finding.get("finding_id"):
                        stored.update(decision)
            if not retained:
                section["status"] = "NOT_ASSESSED"
        section.pop("integrity", None)
        section["integrity"] = {
            "algorithm": "sha256",
            "scope": "stored_report_json_without_compliance_integrity",
            "digest": canonical_hash(clean),
        }
    return clean


def verify_report(report: dict[str, Any]) -> bool:
    import copy

    clone = copy.deepcopy(report)
    section = clone.get("compliance") or {}
    seal = section.pop("integrity", None)
    return bool(seal and seal.get("digest") == canonical_hash(clone))


def _md(value: Any) -> str:
    return str(value or "—").replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def render_compliance(report: dict[str, Any]) -> str:
    section = report.get("compliance")
    lines = [
        "# 규제·가이드라인 검토 권고",
        "",
        f"> {BANNER}",
        "",
        f"- 감사 ID: {_md(report.get('audit_id'))}",
        f"- 실행 코드 버전: {_md(report.get('sentra_build_sha'))}",
        f"- 생성: {_md(report.get('generated_at'))}",
        f"- 대상: {_md(', '.join(t.get('name', '') for t in report.get('targets', [])))}",
        "- 원본 코드 미변경",
        "",
    ]
    if not section:
        return "\n".join(lines + ["이 보고서에는 규제 검토 자료가 저장되지 않았습니다."])
    lines += [
        f"- 매핑 버전: {_md(section.get('catalog_version'))}",
        f"- 매핑 digest: `{_md(section.get('catalog_digest'))}`",
        "",
    ]
    if section.get("draft"):
        lines += ["> [초안] 원문 검토 전 항목이 포함된 기존 형식의 보고서입니다.", ""]
    if section.get("schema") != 2:
        for framework in section.get("frameworks", []):
            for control in framework.get("controls", []):
                lines.append(f"- {_md(control.get('title'))}: {STATES.get(control.get('status'), '미점검')}")
        return "\n".join(lines)
    for limit in section.get("limitations", []):
        lines.append(f"- {_md(limit)}")
    for c in report.get("agent_coverage", []):
        lines.append(
            f"- 실행 범위: {_md(c.get('target_id'))}, 모델 검증 {_md(c.get('model_verification'))}, 공격 검사 {_md(c.get('attacks'))}"
        )
    for item in section.get("recommendations", []):
        lines += [
            "",
            f"## {STATES.get(item['status'], '미점검')} · {_md(item['heading'])}",
            f"- 출처: {_md(item['issuer'])} 「{_md(item['title'])}」 {_md(item.get('version'))}",
            f"- 원문 위치: `{_md(item['control_id'])}`",
            f"- 적용 조건: {_md(item['condition'])}",
            f"- 적용 여부: {APPLICABILITY.get(item.get('applicability', 'REQUIRES_REVIEW'), '적용 여부 확인 필요')}",
            f"- 추가 확인: {_md(item.get('additional_review'))}",
        ]
        if item.get("source_url"):
            lines.append(f"- 게시처: {_md(item['source_url'])}")
        if item.get("page_start"):
            lines.append(f"- PDF 페이지: {item['page_start']}–{item.get('page_end') or item['page_start']}")
        for ev in item["evidence"]:
            lines += [
                f"> {_md(ev['quote'])}",
                f"- 인용 일치 확인됨 · 관련성: {RELEVANCE[ev['relevance']]} · {_md(ev['via'])}",
                f"- 연결 이유: {_md(ev['reason'])}",
            ]
        for f in item["findings"]:
            lines.append(
                f"- Finding {_md(f.get('finding_id'))}: [{_md(f['severity'])}] {_md(f['title'])}, {_md(f['status'])}, {_md(f.get('location'))}"
            )
        for probe in item["probes"]:
            lines.append(
                f"- probe {_md(probe.get('probe_id'))}: 성공/시도 {_md(probe.get('successes'))}/{_md(probe.get('attempts'))}, 오류 {_md(probe.get('error'))}"
            )
    if not section.get("recommendations"):
        lines += ["", "검증된 원문 연결이 없어 검토 권고를 생성하지 않았습니다."]
    if section.get("missing_sources"):
        lines += ["", "## 미확보 원문", *[f"- {_md(s['title'])}" for s in section["missing_sources"]]]
    deferred = [d for d in section.get("mapping_decisions", []) if d["status"] == "DEFERRED"]
    if deferred:
        lines += ["", "## 근거 연결 보류"]
        lines += [
            f"- Finding {_md(d['finding_id'])}: {DEFER_REASONS.get(d['reason'], '추가 검토 필요')}" for d in deferred
        ]
    lines += [
        "",
        f"- 미매핑 Finding: {_md(', '.join(str(x) for x in section.get('unmapped_findings', [])))}",
        f"- 저장 JSON 무결성 SHA256: `{_md(section.get('integrity', {}).get('digest'))}`",
    ]
    return mask_text("\n".join(lines))
