"""Offline link estimation and final report integration using a scripted model, no paid calls."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.compliance import load_catalog, verify_report
from core.compliance_cli import atomic_yaml, generate_links
from core.errors import AppError
from core.settings import get_settings, load_config
from tests.fakes_llm import ScriptedModel
from tests.unit.test_compliance import link


async def test_link_pipeline_validates_quote_and_generates_category_fallback(originals, monkeypatch):
    import core.compliance_cli as cli
    import mcp_servers.rag.regulations as rag

    _, cfg, clauses = originals
    atomic_yaml(
        cfg / "link.yaml",
        {"candidates": 2, "max_tokens": 50000, "max_cost_usd": 1, "max_pairs": 60, "confidence_threshold": 0.8},
    )
    atomic_yaml(cfg.parent / "models.yaml", {"defaults": {"model": "test"}})
    atomic_yaml(cfg.parent / "pricing.yaml", {"models": {}})
    rules = cfg.parent / "rules"
    rules.mkdir()
    atomic_yaml(rules / "R.yaml", {"id": "R", "category": "INDIRECT_INJECTION", "title": "입력 공격"})
    monkeypatch.setenv("RULES_DIR", str(rules))
    get_settings.cache_clear()
    load_config.cache_clear()
    model = ScriptedModel(
        [{"relevant": True, "reason": "합성 테스트 관련성", "confidence": 0.9, "quote": link().quote}]
    )
    monkeypatch.setattr(cli, "llm_available", lambda: True)
    monkeypatch.setattr(cli, "make_chat_model", lambda name: model)
    monkeypatch.setattr(rag, "search_clauses", AsyncMock(return_value=[{"clause_id": "TEST:7-CHK-09"}]))
    monkeypatch.setattr(rag, "exact_clause", AsyncMock(return_value=clauses["TEST:7-CHK-09"].model_dump()))
    result = await generate_links()
    assert result["generated"] == 2
    saved = load_catalog().links
    assert {next(iter(item.key)) for item in saved} == {"category", "rule_id"}
    assert "<untrusted" in model.calls[0][1].content
    # A fabricated quotation fails before replacing the last good links file.
    before = (cfg / "links/generated.yaml").read_bytes()
    model.responses = [{"relevant": True, "reason": "wrong", "confidence": 0.99, "quote": "없는 인용"}]
    with pytest.raises(AppError, match="differs"):
        await generate_links()
    assert (cfg / "links/generated.yaml").read_bytes() == before


async def test_report_finalizes_live_verification_before_mapping(originals, monkeypatch):
    mod = importlib.import_module("core.graph.nodes.report")
    _, cfg, _ = originals
    atomic_yaml(cfg / "links/default.yaml", [link().model_dump()])
    state = {
        "audit_id": "test-audit",
        "targets": [{"target_id": "a", "type": "agent", "name": "synthetic"}],
        "agent_coverage": [
            {
                "target_id": "a",
                "static": "COMPLETED",
                "attacks": "PLANNED",
                "runtime": {"status": "RUNNING", "mode": "live", "requested_mode": "live"},
            }
        ],
        "correlated": [
            {
                "finding_id": "f1",
                "title": "synthetic",
                "severity": "HIGH",
                "confidence": 0.9,
                "category": "INDIRECT_INJECTION",
                "source": "agent",
                "rule_id": "PROBE-P03",
            }
        ],
    }
    saved = []

    async def call(state, node, tool, **args):
        if tool == "get_findings":
            return [{"finding_id": "f1", "status": "OPEN"}]
        if tool == "save_report":
            saved.append(args["report_json"])
            return {"report_id": "r1"}
        return {}

    monkeypatch.setattr(mod, "get_deps", lambda config: SimpleNamespace(call=call, event=AsyncMock()))
    monkeypatch.setattr(mod, "release_target_lock", AsyncMock())
    import core.proxy_sessions as proxy

    monkeypatch.setattr(proxy, "usage", AsyncMock(return_value={"successful_requests": 1}))
    await mod.report.__wrapped__(state, {})
    assert saved[0]["compliance"]["recommendations"][0]["status"] == "REVIEW_RECOMMENDED"
    assert verify_report(saved[0])
    monkeypatch.setattr(proxy, "usage", AsyncMock(return_value={"successful_requests": 0}))
    await mod.report.__wrapped__(state, {})
    assert saved[1]["compliance"]["recommendations"][0]["status"] == "NOT_ASSESSED"
    assert verify_report(saved[1])
