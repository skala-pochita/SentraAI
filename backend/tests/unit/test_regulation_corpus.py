"""Corpus transcription, page locators and persistent local retrieval contracts."""

import json
import sqlite3

import pytest
import yaml

from core.compliance_cli import atomic_yaml, import_source
from core.errors import AppError
from core.regulation_sources import load_clauses, normalize_clauses
from core.settings import get_settings
from mcp_servers.rag.regulations import exact_clause, ingest_regulations, search_clauses


def test_only_actual_checklist_items_have_check_ids():
    source = {
        "id": "TEST",
        "title": "합성 안내",
        "issuer": "테스트",
        "source_type": "kr_guideline",
        "format": "checklist",
    }
    text = "7. 보안성 원칙\n① 본문 예시이다.\n\f[7. 보안성 원칙] 점검 항목\n⑨ 방어체계를 갖추었는가?\n㊳ 클라우드 통제를 적용하는가?"
    clauses = normalize_clauses(source, text)
    assert {c.id for c in clauses} == {"TEST:7-CHK-09", "TEST:7-CHK-38"}
    assert all(c.page_start == c.page_end == 2 for c in clauses)


def test_sections_keep_page_locators_and_original_table_text():
    source = {
        "id": "TEST",
        "title": "합성 안내",
        "issuer": "테스트",
        "source_type": "kr_guideline",
        "format": "sections",
    }
    text = "목차\n제1장 개요\n\f제 1 장 개요\n항목    대상\n위험    모델\n\f제 2 장 대책\n점검한다."
    clauses = normalize_clauses(source, text)
    assert clauses[1].id == "TEST:PAGE-002-01" and clauses[1].page_start == 2
    assert "항목    대상" in clauses[1].text


def test_markdown_and_page_tampering_are_detected(originals):
    root, _, _ = originals
    manifest = yaml.safe_load((root / "MANIFEST.local.yaml").read_text())
    source = manifest["sources"][0]
    md = root / source["markdown_file"]
    assert "## PDF 페이지 1" in md.read_text() and "file_sha256:" in md.read_text()
    original = md.read_bytes()
    md.write_text("tampered")
    with pytest.raises(AppError, match="Markdown hash"):
        load_clauses(root)
    md.write_bytes(original)
    path = root / source["clauses_file"]
    records = yaml.safe_load(path.read_text())
    records["clauses"][0].update(page_start=99, page_end=99)
    atomic_yaml(path, records)
    with pytest.raises(AppError, match="page locator"):
        load_clauses(root)


async def test_local_index_retrieval_exact_lookup_stale_and_tampered(originals, monkeypatch, tmp_path):
    root, _, clauses = originals
    path = tmp_path / "index.sqlite3"
    monkeypatch.setenv("REGULATION_INDEX_PATH", str(path))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()
    first = await ingest_regulations()
    assert first["documents"] == 2 and first["provider"] == "hash"
    assert (await ingest_regulations())["changed"] == 0
    hits = await search_clauses("입력 공격", "TEST", 2)
    assert hits[0]["clause_id"] == "TEST:7-CHK-09"
    assert await exact_clause(hits[0]["clause_id"]) == clauses[hits[0]["clause_id"]].model_dump()
    with pytest.raises(AppError, match="verified clauses"):
        await search_clauses("보도자료", "MISSING", 2)
    with sqlite3.connect(path) as db:
        row = db.execute("SELECT id,data FROM clauses LIMIT 1").fetchone()
        data = json.loads(row[1])
        data["text"] = "인덱스 변조"
        db.execute("UPDATE clauses SET data=? WHERE id=?", (json.dumps(data), row[0]))
    with pytest.raises(AppError, match="differs"):
        await exact_clause(row[0])
    original_file = tmp_path / "changed.txt"
    original_file.write_text("7. 보안성 원칙 점검 항목\n⑨ 변경된 입력 공격 방어를 확인하는가?")
    import_source(root, "TEST", original_file)
    with pytest.raises(AppError, match="stale"):
        await search_clauses("입력 공격", None, 2)
    assert (await ingest_regulations())["documents"] == 1


def test_download_rejects_non_pdf_and_unknown_ids(tmp_path, monkeypatch):
    import httpx

    import core.regulation_download as download

    cfg = tmp_path / "config/compliance"
    atomic_yaml(
        cfg / "downloads.yaml",
        {
            "sources": [
                {
                    "id": "TEST",
                    "attachment_url": "https://example.org/file",
                    "source_url": "https://example.org",
                    "version": "test",
                }
            ]
        },
    )
    monkeypatch.setenv("CONFIG_DIR", str(cfg.parent))
    monkeypatch.setenv("KNOWLEDGE_DIR", str(tmp_path / "knowledge"))
    get_settings.cache_clear()
    client = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b"<html>not a PDF</html>"))
    )
    monkeypatch.setattr(download.httpx, "Client", lambda **kwargs: client)
    with pytest.raises(AppError, match="unknown"):
        download.download_sources(["UNKNOWN"])
    with pytest.raises(AppError, match="not a PDF"):
        download.download_sources(["TEST"])
    assert not (tmp_path / "knowledge/sources/MANIFEST.local.yaml").exists()
    get_settings.cache_clear()
