"""One original clause per KB document/chunk. Exact lookup validates local provenance."""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import delete, select

from core.db.models import KbChunk, KbDocument
from core.db.session import session_scope
from core.embeddings import embed, provider
from core.errors import AppError, ErrorCode
from core.regulation_sources import Clause, load_clauses
from core.settings import get_settings
from mcp_servers.rag.ingest import bump_rag_version
from mcp_servers.rag.search import hybrid_search

REGULATION_SOURCES = ("kr_law", "kr_guideline")


async def ingest_regulations() -> dict[str, int]:
    clauses, _ = load_clauses()
    if not clauses:
        raise AppError(ErrorCode.VALIDATION, "no original clauses available; existing index unchanged")
    async with session_scope() as db:
        rows = list(
            (await db.execute(select(KbDocument).where(KbDocument.source_type.in_(REGULATION_SOURCES)))).scalars()
        )
        existing = {doc.ref_code: doc for doc in rows}
        if len(existing) != len(rows):
            raise AppError(ErrorCode.CONFLICT, "duplicate regulatory references; repair index")
        for old in rows:
            if old.ref_code not in clauses:
                await db.delete(old)
        changed = 0
        for clause in clauses.values():
            content = json.dumps(
                {
                    "clause": clause.model_dump(),
                    "embedding": {
                        "provider": provider(),
                        "model": get_settings().embedding_model if provider() == "openai" else "hash-v1",
                    },
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            doc = existing.get(clause.id)
            if doc and doc.content_md == content:
                continue
            [vector] = await embed([clause.text])
            if doc is None:
                doc = KbDocument(
                    id=uuid.uuid5(uuid.NAMESPACE_URL, "sentraai:clause:" + clause.id),
                    title=f"{clause.title} · {clause.id}",
                    source_type=clause.source_type,
                    ref_code=clause.id,
                    content_md=content,
                )
                db.add(doc)
                await db.flush()
            else:
                doc.title, doc.content_md, doc.source_type = (
                    f"{clause.title} · {clause.id}",
                    content,
                    clause.source_type,
                )
                await db.execute(delete(KbChunk).where(KbChunk.doc_id == doc.id))
            db.add(KbChunk(doc_id=doc.id, section=clause.heading, text=clause.text, embedding=vector))
            changed += 1
    await bump_rag_version()
    return {"documents": len(clauses), "changed": changed, "chunks_per_clause": 1}


async def search_clauses(query: str, source: str | None, k: int) -> list[dict[str, Any]]:
    if not 1 <= k <= 20 or not query.strip():
        raise AppError(ErrorCode.VALIDATION, "query required; k must be 1..20")
    clauses, _ = load_clauses()
    if source and source not in {c.source for c in clauses.values()}:
        raise AppError(ErrorCode.VALIDATION, "source has no verified clauses")
    hits = await hybrid_search(query, REGULATION_SOURCES, k, ref_prefix=(source + ":") if source else None)
    return [{**hit, "clause_id": hit["ref_code"], "candidate_only": True} for hit in hits if hit["ref_code"] in clauses]


async def exact_clause(clause_id: str) -> dict[str, Any]:
    clauses, _ = load_clauses()
    clause = clauses.get(clause_id)
    if clause is None:
        raise AppError(ErrorCode.NOT_FOUND, "clause original unavailable")
    async with session_scope() as db:
        doc = (
            await db.execute(
                select(KbDocument).where(
                    KbDocument.ref_code == clause_id, KbDocument.source_type.in_(REGULATION_SOURCES)
                )
            )
        ).scalar_one_or_none()
        if doc is None:
            raise AppError(ErrorCode.NOT_FOUND, "clause not indexed")
        try:
            payload = json.loads(doc.content_md)
            stored = Clause.model_validate(payload.get("clause", payload))
        except (ValueError, TypeError) as exc:
            raise AppError(ErrorCode.VALIDATION, "invalid clause index") from exc
        if stored != clause:
            raise AppError(ErrorCode.VALIDATION, "stale clause index; reindex original snapshot")
        return stored.model_dump()
