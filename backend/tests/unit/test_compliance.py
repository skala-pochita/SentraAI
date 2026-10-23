"""Original-backed pipeline: synthetic text fixtures, never claimed to be official documents."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime

import pytest
import yaml
from pydantic import ValidationError

from core.compliance import (
    Catalog,
    Link,
    Profile,
    build_compliance,
    links_for,
    load_catalog,
    render_compliance,
    seal_report,
    verify_report,
)
from core.compliance_cli import atomic_yaml, bootstrap_links, merge_links, review_link
from core.errors import AppError
from core.regulation_sources import compact, load_clauses, normalize_clauses, sha256

TEXT = "7. 보안성 원칙 점검 항목\n⑨ 입력 공격에 대한 방어 체계를 구성했는가?\n⑩ 모의 공격을 실행했는가?"


def link(clause="TEST:7-CHK-09", key=None, quote="입력 공격에 대한 방어 체계를 구성했는가?", **kwargs):
    return Link(
        key=key or {"category": "INDIRECT_INJECTION"},
        clause=clause,
        quote=quote,
        quote_sha256=sha256(quote),
        source_sha256=sha256(TEXT),
        reason="합성 테스트 관련성",
        generated_by={"model": "test"},
        **kwargs,
    )


def catalog(originals, links=None):
    _, _, clauses = originals
    return Catalog(version="test", digest="a" * 64, clauses=clauses, links=links or [link()], missing_sources=[])


def finding(**kwargs):
    return {
        "finding_id": "f1",
        "title": "synthetic issue",
        "severity": "HIGH",
        "source": "code",
        "status": "OPEN",
        "category": "INDIRECT_INJECTION",
        "standard_refs": ["REF"],
        **kwargs,
    }


def report(findings=None, **kwargs):
    return {
        "audit_id": "test-audit",
        "generated_at": "2026-10-23T00:00:00Z",
        "targets": [{"name": "synthetic", "type": "repo"}],
        "findings": findings or [],
        "probes": [],
        **kwargs,
    }


def test_law_boundaries_preserve_original_words():
    source = {"id": "LAW", "title": "합성 법령", "issuer": "테스트", "source_type": "kr_law", "format": "law"}
    text = "제34조(합성 책무)\n① 다음 사항을 정한다.\n1. 위험을 점검한다.\n4. 사람의 관리를 둔다.\n② 기록을 남긴다.\n제35조(기타)\n다른 내용"
    clauses = normalize_clauses(source, text)
    assert "LAW:34-1-4" in {c.id for c in clauses}
    assert all(compact(c.text) in compact(text) for c in clauses)
    assert all(c.source_sha256 == sha256(text) for c in clauses)


def test_notice_cannot_be_regulatory_body():
    assert normalize_clauses({"format": "notice"}, "보도자료") == []


def test_source_and_quote_validation_and_stable_digest(originals):
    root, cfg, _ = originals
    atomic_yaml(cfg / "links/default.yaml", [link().model_dump()])
    a = load_catalog(cfg, root)
    b = load_catalog(cfg, root)
    assert a.digest == b.digest
    broken = link().model_dump()
    broken["quote"] = "원문에 없는 문구"
    broken["quote_sha256"] = sha256(broken["quote"])
    atomic_yaml(cfg / "links/default.yaml", [broken])
    with pytest.raises(AppError, match="mismatch"):
        load_catalog(cfg, root)


@pytest.mark.parametrize("part", ["raw", "text", "clauses", "manifest"])
def test_tampered_originals_fail_closed(originals, part):
    root, _, _ = originals
    if part in {"raw", "text"}:
        next((root / part).glob("*")).write_text("변조된 원문")
    elif part == "clauses":
        p = next((root / "clauses").glob("*"))
        data = yaml.safe_load(p.read_text())
        data["clauses"][0]["text"] = "원문에 없는 문구"
        atomic_yaml(p, data)
    else:
        p = root / "MANIFEST.local.yaml"
        data = yaml.safe_load(p.read_text())
        data["sources"][0]["text_sha256"] = "0" * 64
        atomic_yaml(p, data)
    with pytest.raises(AppError):
        load_clauses(root)


def test_path_traversal_blocked(originals):
    root, _, _ = originals
    p = root / "MANIFEST.local.yaml"
    data = yaml.safe_load(p.read_text())
    data["sources"][0]["raw_file"] = "../outside.txt"
    atomic_yaml(p, data)
    with pytest.raises(AppError, match="escapes"):
        load_clauses(root)


def test_missing_inputs_are_not_fabricated(tmp_path):
    (tmp_path / "links").mkdir()
    atomic_yaml(tmp_path / "catalog.yaml", {"version": "x"})
    sources = tmp_path / "sources"
    sources.mkdir()
    atomic_yaml(sources / "MANIFEST.yaml", {"sources": [{"id": "NONE", "title": "미확보", "status": "MISSING"}]})
    c = load_catalog(tmp_path, sources)
    data = build_compliance(report([finding()]), c)
    assert data["status"] == "NOT_ASSESSED" and not data["recommendations"]
    assert data["unmapped_findings"] == ["f1"] and data["missing_sources"]


def test_priority_multi_mapping_and_rejection_blocks_fallback(originals):
    precise = link(key={"rule_id": "R"})
    second = link(clause="TEST:7-CHK-10", quote="모의 공격을 실행했는가?")
    c = catalog(originals, [precise, link(), second, link(key={"standard_ref": "REF"})])
    assert links_for(finding(rule_id="R"), c) == [precise]
    assert len(links_for(finding(), c)) == 2
    assert links_for(finding(category="UNKNOWN"), c)[0].key == {"standard_ref": "REF"}
    c.links[0] = precise.model_copy(update={"relevance": "rejected"})
    assert links_for(finding(rule_id="R"), c) == []


@pytest.mark.parametrize(
    "status,verdict,expected",
    [
        ("OPEN", None, "REVIEW_RECOMMENDED"),
        ("FIXING", None, "REVIEW_RECOMMENDED"),
        ("ACCEPTED_RISK", None, "REVIEW_RECOMMENDED"),
        ("MANUAL_REQUIRED", None, "REVIEW_RECOMMENDED"),
        ("FALSE_POSITIVE", None, "NOT_ASSESSED"),
        ("OPEN", "FALSE_POSITIVE", "NOT_ASSESSED"),
        ("RESOLVED", None, "NOT_ASSESSED"),
    ],
)
def test_evidence_status(originals, status, verdict, expected):
    result = build_compliance(report([finding(status=status, verdict=verdict)]), catalog(originals))
    assert result["recommendations"][0]["status"] == expected


def test_mock_and_unverified_probe_not_clean_or_live_evidence(originals):
    c = catalog(originals)
    r = report([finding()], agent_coverage=[{"model_verification": "MOCK", "runtime": {"mode": "mock"}}])
    assert build_compliance(r, c)["recommendations"][0]["status"] == "NOT_ASSESSED"
    r = report([finding(rule_id="PROBE-P03", source="agent")], agent_coverage=[{"model_verification": "UNVERIFIED"}])
    assert build_compliance(r, c)["recommendations"][0]["status"] == "NOT_ASSESSED"


@pytest.mark.parametrize(
    "error,benign_success,expected",
    [(None, 3, "NO_RELATED_FINDING"), ("timeout", 3, "NOT_ASSESSED"), (None, 0, "NOT_ASSESSED")],
)
def test_clean_evidence_requires_live_successful_positive_control(originals, error, benign_success, expected):
    r = report(
        agent_coverage=[{"model_verification": "LIVE_VERIFIED", "attacks": "COMPLETED"}],
        probes=[
            {"category": "INDIRECT_INJECTION", "attempts": 3, "successes": 0, "error": error},
            {"category": "BENIGN_CONTROL", "attempts": 3, "successes": benign_success},
        ],
    )
    assert build_compliance(r, catalog(originals))["recommendations"][0]["status"] == expected


def test_render_snapshot_hash_masking_and_legacy(originals):
    r = report([finding(title="secret sk-abcdefghijklmnopqrstuv")])
    r["compliance"] = build_compliance(r, catalog(originals), {"sector": "general", "high_impact_ai": "NO"})
    sealed = seal_report(r)
    assert verify_report(sealed) and seal_report(sealed) == sealed
    assert "sk-abcdefghijklmnopqrstuv" not in json.dumps(sealed)
    md = render_compliance(sealed)
    assert "검토 권고" in md and "인용 일치 확인됨" in md and "AI 추정" in md and "참고 자료" in md
    assert not any(word in md for word in ["준수합니다", "위반", "적합합니다"])
    tampered = copy.deepcopy(sealed)
    tampered["findings"][0]["title"] = "changed"
    assert not verify_report(tampered)
    legacy = {"compliance": {"draft": True, "frameworks": [{"controls": [{"title": "old", "status": "ISSUES_OPEN"}]}]}}
    assert "[초안]" in render_compliance(legacy) and "old" in render_compliance(legacy)


def test_sensitive_quote_cannot_keep_verified_label(originals):
    c = catalog(originals)
    q = "sk-abcdefghijklmnopqrstuv"
    c.clauses["TEST:7-CHK-09"].text = q
    c.links = [link(quote=q)]
    r = report([finding()])
    r["compliance"] = build_compliance(r, c)
    sealed = seal_report(r)
    assert sealed["compliance"]["recommendations"] == [] and verify_report(sealed)


def test_human_decisions_preserved_and_review_is_explicit(originals):
    _, cfg, _ = originals
    human = link(relevance="human_confirmed", confirmed_by="reviewer", confirmed_at=datetime.now(UTC).isoformat())
    assert merge_links([human], [link()]) == [human]
    rejected = human.model_copy(update={"relevance": "rejected"})
    assert merge_links([rejected], [link()]) == [rejected]
    atomic_yaml(cfg / "links/default.yaml", [link().model_dump()])
    review_link(link().identity, "human_confirmed", "reviewer")
    assert load_catalog().links[0].relevance == "human_confirmed"
    with pytest.raises(ValidationError):
        link(relevance="human_confirmed")


def test_bootstrap_requires_original_and_preserves_confirmed(originals):
    root, cfg, _ = originals
    atomic_yaml(
        cfg / "initial_candidates.yaml",
        [
            {
                "key": {"category": "INDIRECT_INJECTION"},
                "source": "TEST",
                "clause": "TEST:7-CHK-09",
                "quote": link().quote,
            },
            {"key": {"category": "UNKNOWN"}, "source": "TEST", "quote": "원문에 없는 문구"},
        ],
    )
    assert bootstrap_links(root) == {"candidates": 2, "verified_links": 1, "unavailable": 1}
    review_link(link().identity, "rejected", "reviewer")
    bootstrap_links(root)
    assert load_catalog().links[0].relevance == "rejected"


def test_profile_rejects_invalid_input():
    with pytest.raises(ValidationError):
        Profile(high_impact_ai="MAYBE")
    with pytest.raises(ValidationError):
        Profile(sector="finance", extra="injected")
