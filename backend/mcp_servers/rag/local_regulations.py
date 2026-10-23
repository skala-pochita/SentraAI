"""Explicit, persistent standalone RAG for regulatory originals (no PostgreSQL required).

Stores the same pinned clauses and embeddings as the server index. Hash embeddings
are an offline keyword approximation, not a substitute for semantic embeddings.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from pathlib import Path
from typing import Any

from core.embeddings import DIM, embed, provider
from core.errors import AppError, ErrorCode
from core.regulation_sources import Clause, canonical_hash, load_clauses
from core.settings import get_settings


def fingerprint() -> dict[str, str]:
    return {"provider": provider(), "model": get_settings().embedding_model if provider() == "openai" else "hash-v1"}


def snapshot(clauses: dict[str, Clause]) -> str:
    return canonical_hash({"embedding": fingerprint(), "clauses": [clauses[c].model_dump() for c in sorted(clauses)]})


def connect(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise AppError(ErrorCode.NOT_FOUND, "local regulation index missing; run compliance_cli index")
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def check_snapshot(db: sqlite3.Connection, clauses: dict[str, Clause]) -> None:
    row = db.execute("SELECT value FROM metadata WHERE key = 'snapshot'").fetchone()
    if not row or row[0] != snapshot(clauses):
        raise AppError(ErrorCode.VALIDATION, "stale local regulation index; reindex original snapshot")


async def ingest(path: Path) -> dict[str, Any]:
    clauses, _ = load_clauses()
    if not clauses:
        raise AppError(ErrorCode.VALIDATION, "no original clauses available; existing index unchanged")
    digest = snapshot(clauses)
    if path.is_file():
        with connect(path) as db:
            previous = db.execute("SELECT value FROM metadata WHERE key = 'snapshot'").fetchone()
            if previous and previous[0] == digest:
                return {"documents": len(clauses), "changed": 0, "backend": "sqlite", **fingerprint()}
    # Compute all vectors before the atomic DB transaction; failed requests leave
    # the previous searchable corpus intact.
    items = list(clauses.values())
    vectors = []
    for offset in range(0, len(items), 32):
        vectors.extend(await embed([c.text for c in items[offset : offset + 32]]))
    if len(vectors) != len(items) or any(len(v) != DIM or not all(math.isfinite(x) for x in v) for v in vectors):
        raise AppError(ErrorCode.VALIDATION, "invalid embedding result")
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute(
            "CREATE TABLE IF NOT EXISTS clauses (id TEXT PRIMARY KEY, source TEXT NOT NULL, data TEXT NOT NULL, vector TEXT NOT NULL)"
        )
        db.execute("DELETE FROM clauses")
        db.executemany(
            "INSERT INTO clauses VALUES (?,?,?,?)",
            [(c.id, c.source, c.model_dump_json(), json.dumps(v)) for c, v in zip(items, vectors, strict=True)],
        )
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('snapshot', ?)", (digest,))
        db.execute("INSERT OR REPLACE INTO metadata VALUES ('embedding', ?)", (json.dumps(fingerprint()),))
    return {"documents": len(clauses), "changed": len(clauses), "backend": "sqlite", **fingerprint()}


def verified(data: str, originals: dict[str, Clause]) -> Clause:
    clause = Clause.model_validate_json(data)
    if originals.get(clause.id) != clause:
        raise AppError(ErrorCode.VALIDATION, "local clause index differs from original")
    return clause


async def search(path: Path, query: str, source: str | None, k: int) -> list[dict[str, Any]]:
    originals, _ = load_clauses()
    with connect(path) as db:
        check_snapshot(db, originals)
        rows = db.execute(
            "SELECT data, vector FROM clauses WHERE (? IS NULL OR source = ?)", (source, source)
        ).fetchall()
    [query_vector] = await embed([query])
    tokens = set(re.findall(r"[A-Za-z0-9_]+|[가-힣]{2,}", query.lower()))
    hits = []
    for data, encoded_vector in rows:
        clause = verified(data, originals)
        vector = json.loads(encoded_vector)
        cosine = max(0.0, sum(a * b for a, b in zip(query_vector, vector, strict=True)))
        keyword = sum(token in clause.text.lower() for token in tokens) / max(1, len(tokens))
        score = 0.5 * cosine + 0.5 * keyword
        if score <= 0:
            continue
        hits.append(
            {
                "clause_id": clause.id,
                "ref_code": clause.id,
                "chunk_id": clause.id,
                "doc_id": clause.id,
                "doc_title": clause.title,
                "section": clause.heading,
                "source_type": clause.source_type,
                "text": clause.text,
                "score": round(score, 6),
                "page_start": clause.page_start,
                "page_end": clause.page_end,
                "candidate_only": True,
                "backend": "sqlite",
                "embedding": fingerprint(),
            }
        )
    return sorted(hits, key=lambda h: (-h["score"], h["clause_id"]))[:k]


def exact(path: Path, clause_id: str) -> dict[str, Any]:
    originals, _ = load_clauses()
    with connect(path) as db:
        check_snapshot(db, originals)
        row = db.execute("SELECT data FROM clauses WHERE id = ?", (clause_id,)).fetchone()
    if not row:
        raise AppError(ErrorCode.NOT_FOUND, "clause not indexed")
    return verified(row[0], originals).model_dump()
