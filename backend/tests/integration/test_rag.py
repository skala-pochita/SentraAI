"""Phase 4: 지식 문서 인덱싱 + 하이브리드 검색 + 오탐 규칙 (실제 Postgres/pgvector, 해시 임베딩)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest

from core.db.models import KB_SOURCE_TYPES, Audit, Finding, Target, User
from core.db.session import session_scope
from core.settings import get_settings
from mcp_servers.common import CallContext, default_call
from mcp_servers.rag.ingest import chunk_markdown, count_tokens, ingest_dir
from mcp_servers.rag.search import hybrid_search
from mcp_servers.rag.tools import knowledge


@pytest.fixture
async def indexed(db_ready: None, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()
    async with session_scope() as db:
        await ingest_dir(db, get_settings().knowledge_dir)
    yield


def test_chunking_respects_headers_and_limit() -> None:
    md = "# T\n\n## A\n" + ("가나다라마 " * 400) + "\n\n" + ("바사아자차 " * 400) + "\n\n## B\nshort"
    chunks = chunk_markdown("T", md)
    assert {c.section for c in chunks} >= {"A", "B"}
    assert all(count_tokens(c.text) <= 800 + 120 for c in chunks)
    assert len([c for c in chunks if c.section == "A"]) >= 2


async def test_permitall_query_ranks_spring_guide_top3(indexed: None) -> None:
    res = await hybrid_search("permitAll 올바른 설정", KB_SOURCE_TYPES, k=3)
    titles = [r["doc_title"] for r in res]
    assert any("Spring Security" in t for t in titles), titles


async def test_standard_search_tool_and_cache(indexed: None) -> None:
    token = default_call.set(CallContext("u", "ADMIN", None, "r"))
    try:
        res = await knowledge.search_security_standard(query="간접 프롬프트 인젝션 문서", source="owasp_llm", k=3)
        assert res["ok"] and res["data"][0]["ref_code"] == "OWASP-LLM-2025"
        again = await knowledge.search_security_standard(query="간접 프롬프트 인젝션 문서", source="owasp_llm", k=3)
        assert again["data"] == res["data"]
        denied = await knowledge.search_security_standard(query="x", k=50)
        assert denied["error"]["code"] == "VALIDATION"
    finally:
        default_call.reset(token)


async def test_ingest_update_delete_requires_admin(indexed: None) -> None:
    token = default_call.set(CallContext("u", "DEVELOPER", None, "r"))
    try:
        res = await knowledge.ingest_document(title="x", source_type="policy", content_md="# x\nbody")
        assert res["error"]["code"] == "FORBIDDEN"
    finally:
        default_call.reset(token)
    token = default_call.set(CallContext("u", "ADMIN", None, "r"))
    try:
        res = await knowledge.ingest_document(title="임시 정책", source_type="policy",
                                              content_md="# 임시\n## 규칙\n쿠버네티스 시크릿 회전 주기 90일")
        doc_id = res["data"]["doc_id"]
        hits = await knowledge.search_company_policy(query="쿠버네티스 시크릿 회전", k=3)
        assert hits["data"][0]["doc_title"] == "임시 정책"
        upd = await knowledge.update_document(doc_id=doc_id, content_md="# 임시\n## 규칙\n회전 주기 30일")
        assert upd["ok"]
        assert (await knowledge.delete_document(doc_id=doc_id))["ok"]
        assert (await knowledge.get_document(doc_id=doc_id))["error"]["code"] == "NOT_FOUND"
    finally:
        default_call.reset(token)


async def test_register_false_positive_flow(indexed: None) -> None:
    async with session_scope() as db:
        u = User(email="a@x.io", password_hash="x", name="a", role="ADMIN")
        db.add(u)
        t = Target(type="repo", name="r", ref="/r")
        db.add(t)
        await db.flush()
        a = Audit(requested_by=u.id, mode="full", status="RUNNING", thread_id="t")
        db.add(a)
        await db.flush()
        f = Finding(audit_id=a.id, target_id=t.id, source="code", category="SECCONFIG_CSRF", rule_id="SEC-SPRING-002",
                    title="csrf", severity="MEDIUM", confidence=0.5, fingerprint="fp1", evidence=[],
                    reasoning_summary="r")
        db.add(f)
        await db.flush()
        fid, uid, tid = str(f.id), str(u.id), str(t.id)
    token = default_call.set(CallContext(uid, "DEVELOPER", None, "r"))
    try:
        res = await knowledge.register_false_positive(finding_id=fid, reason="stateless JWT API")
        assert res["ok"] and res["data"]["finding_status"] == "FALSE_POSITIVE"
        rules = await knowledge.list_false_positive_rules(target_id=tid)
        assert rules["data"][0]["fingerprint"] == "fp1"
        missing = await knowledge.register_false_positive(finding_id=str(uuid.uuid4()), reason="x")
        assert missing["error"]["code"] == "NOT_FOUND"
    finally:
        default_call.reset(token)
