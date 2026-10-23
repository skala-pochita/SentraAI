"""Safety and abstention contracts added after the five-document QA."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from core.compliance import (
    build_compliance,
    build_compliance_safely,
    links_for,
    load_catalog,
    render_compliance,
    seal_report,
    verify_report,
)
from core.compliance_cli import atomic_yaml, merge_links
from core.errors import AppError, ErrorCode
from core.regulation_sources import classify_clauses, evidence_candidate, load_clauses
from core.settings import get_settings
from mcp_servers.rag.regulations import ingest_regulations, search_clauses
from tests.unit.test_compliance import catalog, finding, link, report


def test_unknown_pages_and_examples_cannot_attach(originals):
    root, _, clauses = originals
    c = clauses["TEST:7-CHK-09"]
    page = c.model_copy(
        update={"id": "TEST:PAGE-001-01", "content_kind": "recommendation", "classification_basis": "curated_excerpt"}
    )
    classified = classify_clauses(root, {page.id: page})
    assert not evidence_candidate(classified[page.id])  # Local flags cannot promote a legacy page.
    cat = catalog(originals, [link(clause=page.id)])
    cat.clauses = classified
    rep = report([finding()])
    out = build_compliance(rep, cat)
    assert not out["recommendations"]
    assert out["mapping_decisions"][0]["reason"] == "EVIDENCE_NOT_ELIGIBLE"
    assert rep["findings"][0]["title"] == "synthetic issue"


def test_curated_excerpt_must_match_pinned_parent(originals):
    root, _, clauses = originals
    parent = clauses["TEST:7-CHK-09"]
    item = {
        "parent_id": parent.id,
        "slug": "TEST",
        "text": "입력 공격에 대한 방어 체계를 구성했는가?",
        "source_sha256": parent.source_sha256,
        "heading": "합성 방어",
        "content_kind": "recommendation",
    }
    atomic_yaml(root / "evidence_policy.yaml", {"excerpts": [item]})
    out, _ = load_clauses(root)
    assert evidence_candidate(out[parent.id + ":EVIDENCE-TEST"])
    assert out[parent.id + ":EVIDENCE-TEST"].parent_id == parent.id
    item["source_sha256"] = "0" * 64
    atomic_yaml(root / "evidence_policy.yaml", {"excerpts": [item]})
    with pytest.raises(AppError, match="stale"):
        load_clauses(root)


async def test_unrelated_query_returns_no_candidates(originals, monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("REGULATION_INDEX_PATH", str(tmp_path / "index.sqlite"))
    get_settings.cache_clear()
    await ingest_regulations()
    assert await search_clauses("banana galaxy xylophone 9f83a24b", None, 5) == []
    assert await search_clauses("바나나 은하 실로폰 9f83a24b", None, 5) == []
    assert await search_clauses("입력 공격", None, 5)


def test_missing_required_and_out_of_scope_are_distinct(originals):
    root, _, _ = originals
    import yaml

    p = root / "MANIFEST.yaml"
    manifest = yaml.safe_load(p.read_text())
    manifest["sources"] += [
        {"id": "REQUIRED", "title": "필수 자료", "status": "MISSING"},
        {"id": "RMF", "title": "범위 밖", "status": "MISSING", "scope_role": "excluded"},
        {"id": "NOTICE", "title": "참고 자료", "status": "MISSING", "scope_role": "reference"},
    ]
    atomic_yaml(p, manifest)
    _, missing = load_clauses(root)
    assert [s["source"] for s in missing] == ["REQUIRED"]


def test_human_confirmed_mapping_has_priority(originals):
    confirmed = link(relevance="human_confirmed", confirmed_by="reviewer", confirmed_at=datetime.now(UTC).isoformat())
    estimated = link(clause="TEST:7-CHK-10", quote="모의 공격을 실행했는가?")
    cat = catalog(originals, [confirmed, estimated])
    assert links_for(finding(), cat) == [confirmed]


@pytest.mark.parametrize("decision", ["human_confirmed", "rejected"])
def test_source_change_requires_review_without_losing_history(decision):
    previous = link(relevance=decision, confirmed_by="reviewer", confirmed_at=datetime.now(UTC).isoformat())
    generated = link().model_copy(update={"source_sha256": "0" * 64})
    merged = merge_links([previous], [generated])[0]
    assert merged.relevance == "review_required"
    assert merged.confirmed_by is None
    assert merged.generated_by["previous_review"]["relevance"] == decision
    assert merge_links([merged], [generated])[0] == merged  # A rerun cannot silently enable it.


def test_rejected_and_changed_links_remain_deferred(originals):
    changed = link().model_copy(update={"relevance": "review_required"})
    rep = report([finding()])
    out = build_compliance(rep, catalog(originals, [changed]))
    assert out["mapping_decisions"][0]["reason"] == "SOURCE_CHANGED"
    assert rep["findings"][0]["compliance_refs"] == []


def test_bad_catalog_preserves_findings(monkeypatch):
    import core.compliance as compliance

    monkeypatch.setattr(
        compliance, "load_catalog", lambda: (_ for _ in ()).throw(AppError(ErrorCode.VALIDATION, "bad source"))
    )
    rep = report([finding()])
    before = copy.deepcopy(rep["findings"][0])
    rep["compliance"] = build_compliance_safely(rep)
    saved = seal_report(rep)
    assert all(saved["findings"][0][k] == v for k, v in before.items())
    assert saved["compliance"]["mapping_decisions"][0]["reason"] == "SOURCE_MISMATCH"
    assert verify_report(saved)
    assert "원문·인용 검증 실패" in render_compliance(saved)


@pytest.mark.parametrize(
    ("profile", "expected"),
    [({}, "REQUIRES_REVIEW"), ({"sector": "general"}, "REFERENCE_ONLY"), ({"sector": "finance"}, "REQUIRES_REVIEW")],
)
def test_applicability_is_never_established_by_search(originals, profile, expected):
    rep = report([finding()])
    out = build_compliance(rep, catalog(originals), profile)
    assert out["recommendations"][0]["applicability"] == expected


async def test_postgres_candidates_use_same_gate_without_infra(originals, monkeypatch):
    import mcp_servers.rag.regulations as rag

    _, _, clauses = originals
    monkeypatch.delenv("REGULATION_INDEX_PATH", raising=False)
    get_settings.cache_clear()
    c = clauses["TEST:7-CHK-09"]
    query = AsyncMock(return_value=[{"ref_code": c.id, "text": c.text, "score": 0.9}])
    monkeypatch.setattr(rag, "hybrid_search", query)
    assert not await search_clauses("banana galaxy xylophone", None, 5)
    assert await search_clauses("입력 공격", None, 5)
    query.return_value[0]["text"] = "stale index text"
    assert not await search_clauses("입력 공격", None, 5)


def test_saved_snapshot_and_markdown_keep_deferred_reason(originals):
    rep = report([finding(category="UNRELATED")])
    rep["compliance"] = build_compliance(rep, catalog(originals))
    saved = json.loads(json.dumps(seal_report(rep)))
    assert verify_report(saved)
    assert "근거 연결 보류" in render_compliance(saved)
    assert "검토된 관련 근거 연결 없음" in render_compliance(saved)


def test_reimported_source_quarantines_review_and_bootstrap_cannot_reconfirm(originals, tmp_path):
    from core.compliance_cli import bootstrap_links, import_source

    root, cfg, _ = originals
    reviewed = link(relevance="human_confirmed", confirmed_by="reviewer", confirmed_at=datetime.now(UTC).isoformat())
    atomic_yaml(cfg / "links/default.yaml", [])
    atomic_yaml(cfg / "links/generated.yaml", [reviewed.model_dump()])
    text = tmp_path / "updated.txt"
    text.write_text("7. 보안성 원칙 점검 항목\n⑨ 입력 공격에 대한 방어 체계를 구성했는가?\n⑩ 변경된 훈련을 실행했는가?")
    import_source(root, "TEST", text)
    cat = load_catalog()
    assert len(cat.review_queue) == 1 and not links_for(finding(), cat)
    out = build_compliance(report([finding()]), cat)
    assert out["mapping_decisions"][0]["reason"] == "SOURCE_CHANGED"
    atomic_yaml(
        cfg / "initial_candidates.yaml",
        [{"key": reviewed.key, "source": "TEST", "clause": reviewed.clause, "quote": reviewed.quote}],
    )
    bootstrap_links(root)
    regenerated = load_catalog()
    assert not regenerated.review_queue
    assert regenerated.links[0].relevance == "review_required"
    assert regenerated.links[0].generated_by["previous_review"]["confirmed_by"] == "reviewer"
    assert not links_for(finding(), regenerated)


def test_assistant_hold_is_not_mislabeled_as_source_change(originals):
    held = link().model_copy(
        update={
            "relevance": "review_required",
            "generated_by": {"assistant_review": {"action": "hold", "human_confirmed": False}},
        }
    )
    rep = report([finding()])
    out = build_compliance(rep, catalog(originals, [held]))
    assert out["mapping_decisions"][0]["reason"] == "RELEVANCE_REVIEW_REQUIRED"
    rep["compliance"] = out
    assert "문제와 조항의 관련성 또는 적용 전제 추가 확인 필요" in render_compliance(rep)


@pytest.mark.parametrize("condition", ["finance_customer_impact", "finance_high_risk"])
def test_narrow_finance_conditions_remain_reference_for_general_sector(originals, condition):
    cat = catalog(originals)
    cat.clauses["TEST:7-CHK-09"].condition = condition
    out = build_compliance(report([finding()]), cat, {"sector": "general"})
    assert out["recommendations"][0]["applicability"] == "REFERENCE_ONLY"
    assert "금융 업무" in out["recommendations"][0]["condition"]
