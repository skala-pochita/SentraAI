"""Actual PostgreSQL/pgvector indexing/search/exact lookup. Skips when Postgres is absent."""

from __future__ import annotations

import json

from sqlalchemy import func, select

from core.db.models import KbChunk, KbDocument
from core.db.session import session_scope
from core.errors import AppError
from core.settings import get_settings
from mcp_servers.rag.regulations import exact_clause, ingest_regulations, search_clauses


async def test_one_clause_one_chunk_idempotent_exact_retrieval(originals, db_ready, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()
    first = await ingest_regulations()
    assert first == {"documents": 2, "changed": 2, "chunks_per_clause": 1}
    assert (await ingest_regulations())["changed"] == 0
    async with session_scope() as db:
        assert (await db.execute(select(func.count()).select_from(KbChunk))).scalar_one() == 2
    clause = await exact_clause("TEST:7-CHK-09")
    assert clause["text"].startswith("⑨") and clause["source_sha256"]
    hits = await search_clauses("입력 공격 방어", "TEST", 2)
    assert hits and all(hit["clause_id"].startswith("TEST:") and hit["candidate_only"] for hit in hits)


async def test_stale_index_is_not_used_as_a_quote(originals, db_ready, monkeypatch):
    import pytest

    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()
    await ingest_regulations()
    async with session_scope() as db:
        doc = (await db.execute(select(KbDocument).where(KbDocument.ref_code == "TEST:7-CHK-09"))).scalar_one()
        value = json.loads(doc.content_md)
        value["clause"]["text"] = "원문과 다른 텍스트"
        doc.content_md = json.dumps(value)
    with pytest.raises(AppError, match="stale"):
        await exact_clause("TEST:7-CHK-09")
