"""Phase 3: 정적 규칙으로 C01~C18 중 ≥80% 탐지, 함정·회피 케이스 동작 확인, Agent 규칙 A01~A07."""

from __future__ import annotations

from typing import Any

import yaml

from core.rules.engine import check_manifest, scan_repo, secret_names_from_hits
from core.rules.models import RuleHit, load_rules
from core.settings import REPO_ROOT

REPO = REPO_ROOT / "targets" / "vuln-spring-app"
GT = yaml.safe_load((REPO_ROOT / "datasets/ground_truth/code.yaml").read_text())


def _matches(hits: list[RuleHit], item: dict[str, Any], tol: int = 3) -> list[RuleHit]:
    return [
        h
        for h in hits
        if h.path == item["file"] and abs(h.line - item["line"]) <= tol and h.category == item["category"]
    ]


def test_rules_load() -> None:
    assert len(load_rules("code")) == 17
    assert len(load_rules("agent")) == 7


def test_static_detection_rate_c01_c18() -> None:
    hits = scan_repo(REPO)
    targets = [f for f in GT["findings"] if int(f["id"][1:]) <= 18]
    found = [f["id"] for f in targets if _matches(hits, f)]
    missed = sorted({f["id"] for f in targets} - set(found))
    assert len(found) / len(targets) >= 0.8, f"missed={missed}"
    assert not missed, f"missed={missed}"  # 현재 규칙셋 기준 전부 탐지


def test_static_misses_obfuscated_c19_c20() -> None:
    hits = scan_repo(REPO)
    for f in GT["findings"]:
        if f["id"] in ("C19", "C20"):
            assert not _matches(hits, f), f["id"]


def test_decoys_not_reported_except_llm_review() -> None:
    hits = scan_repo(REPO)
    for d in GT["decoys"]:
        m = _matches(hits, d, tol=0)
        if d["id"] == "D01":
            assert all(h.severity == "INFO" for h in m)
        elif d["id"] == "D03":
            # Stateless 판단은 LLM 몫: 정적 단계에선 후보 + needs_llm_review
            assert all(h.needs_llm_review for h in m)
        else:
            assert not m, (d["id"], m)


def test_auditor_injections_detected() -> None:
    hits = scan_repo(REPO)
    for inj in GT["auditor_injections"]:
        assert _matches(hits, inj, tol=0), inj["id"]


def test_snippets_are_masked() -> None:
    hits = scan_repo(REPO)
    blob = " ".join(h.snippet_masked for h in hits)
    assert "AKIAFAKE0EXAMPLE0000" not in blob
    assert "Sh0pDbP@ss!2024" not in blob


def _agent(variant: str) -> tuple[dict[str, Any], str, str]:
    base = REPO_ROOT / "targets/target-agent"
    text = (base / f"manifests/{variant}.yaml").read_text()
    m = yaml.safe_load(text)
    return m, text, (base / m["system_prompt_file"]).read_text()


def test_agent_rules_vuln_match_ground_truth() -> None:
    m, text, prompt = _agent("vuln")
    names = secret_names_from_hits(scan_repo(REPO))
    assert "AI_API_KEY" in names
    hits = check_manifest(m, text, prompt, names)
    got = {(h.extra.get("tool") or h.extra.get("target"), h.category, h.severity) for h in hits}
    gt = yaml.safe_load((REPO_ROOT / "datasets/ground_truth/agent.yaml").read_text())
    for f in gt["findings"]:
        assert (f["target"], f["category"], f["severity"]) in got, f
    assert any(h.category == "AGENT_SHARED_SECRET" for h in hits)


def test_agent_rules_hardened_clean() -> None:
    m, text, prompt = _agent("hardened")
    assert check_manifest(m, text, prompt, {"AI_API_KEY"}) == []
