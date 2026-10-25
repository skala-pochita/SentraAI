"""A fresh checkout/CLI must ship usable originals, links and offline retrieval."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from core.compliance import build_compliance, load_catalog, render_compliance
from core.regulation_sources import (
    effective_regulation_index,
    effective_sources_root,
    load_clauses,
    sha256,
)
from core.settings import get_settings
from mcp_servers.rag.regulations import exact_clause, search_clauses

REPO = Path(__file__).resolve().parents[3]
BUNDLE = REPO / "knowledge/regulation_bundle"


def test_bundle_inventory_and_catalog():
    inventory = json.loads((BUNDLE / "INVENTORY.json").read_text(encoding="utf-8"))
    for relative, digest in inventory["files"].items():
        assert sha256((BUNDLE / relative).read_bytes()) == digest, relative
    catalog = load_catalog(BUNDLE / "config", BUNDLE / "sources")
    assert not catalog.missing_sources
    assert len(catalog.clauses) == inventory["clauses"]
    assert len(catalog.links) == inventory["links"] > 0
    assert len({c.source for c in catalog.clauses.values()}) == 5


@pytest.mark.parametrize("cli", [False, True])
async def test_fresh_install_selects_bundled_rag_with_model_key(tmp_path, monkeypatch, cli):
    knowledge = tmp_path / "knowledge"
    shutil.copytree(BUNDLE, knowledge / "regulation_bundle")
    sources = knowledge / "sources"
    sources.mkdir()
    shutil.copyfile(
        REPO / ("cli/resources/MANIFEST.yaml" if cli else "knowledge/sources/MANIFEST.yaml"), sources / "MANIFEST.yaml"
    )
    monkeypatch.setenv("KNOWLEDGE_DIR", str(knowledge))
    monkeypatch.delenv("REGULATION_INDEX_PATH", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-key-must-never-be-called")
    get_settings.cache_clear()
    try:
        assert effective_sources_root() == knowledge / "regulation_bundle/sources"
        assert effective_regulation_index() == knowledge / "regulation_bundle/regulations.sqlite3"
        catalog = load_catalog()
        assert not catalog.missing_sources and catalog.links
        report = {
            "findings": [
                {
                    "finding_id": "f1",
                    "category": "INDIRECT_INJECTION",
                    "status": "OPEN",
                    "severity": "HIGH",
                    "title": "Bundle verification fixture",
                }
            ],
            "agent_coverage": [{"model_verification": "MOCK"}],
            "targets": [],
        }
        report["compliance"] = build_compliance(report, catalog)
        assert not report["compliance"]["missing_sources"]
        assert "## 미확보 원문" not in render_compliance(report)
        hits = await search_clauses("프롬프트 인젝션", "FSC-AI-GL", 5)
        assert hits and all(h["embedding"]["provider"] == "hash" for h in hits)
        exact = await exact_clause(hits[0]["clause_id"])
        assert exact["text"] == hits[0]["text"]
    finally:
        get_settings.cache_clear()


def test_local_import_is_not_replaced_by_bundle(originals):
    root, _, clauses = originals
    shutil.copytree(BUNDLE, root.parent / "regulation_bundle")
    assert effective_sources_root() == root
    assert set(load_clauses()[0]) == set(clauses)
    assert effective_regulation_index() is None


def test_explicit_missing_source_root_does_not_fall_back(tmp_path):
    shutil.copyfile(REPO / "knowledge/sources/MANIFEST.yaml", tmp_path / "MANIFEST.yaml")
    clauses, missing = load_clauses(tmp_path)
    assert not clauses and len(missing) == 5
