"""하이브리드 검색 (명세 9.4): pgvector 코사인 + PostgreSQL tsvector 키워드, 점수 가중 합. 결과 캐시 1h."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from sqlalchemy import text

from core.db.session import session_scope
from core.embeddings import embed, provider
from core.redis.cache import cache_get, cache_set
from core.redis.client import get_redis
from core.settings import load_config

_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_]+|\d+|[가-힣]{2,}")

STANDARD_SOURCES = ("owasp_top10", "owasp_llm", "owasp_agentic", "cwe")


def tsquery(query: str) -> str:
    toks = sorted({t.lower() for t in _TOKEN.findall(query)})
    return " | ".join(f"{t}:*" for t in toks)


_SQL = """
WITH vec AS (
  SELECT c.id, 1 - (c.embedding <=> CAST(:emb AS vector)) AS vscore
  FROM kb_chunks c JOIN kb_documents d ON d.id = c.doc_id
  WHERE d.source_type = ANY(:sources)
  ORDER BY c.embedding <=> CAST(:emb AS vector) LIMIT :n
), kw AS (
  SELECT c.id, ts_rank_cd(c.tsv, to_tsquery('simple', :tsq)) AS kscore
  FROM kb_chunks c JOIN kb_documents d ON d.id = c.doc_id
  WHERE d.source_type = ANY(:sources) AND :tsq <> '' AND c.tsv @@ to_tsquery('simple', :tsq)
  ORDER BY kscore DESC LIMIT :n
)
SELECT c.id, c.section, c.text, d.id AS doc_id, d.title, d.ref_code, d.source_type,
       COALESCE(vec.vscore, 0) AS vscore, COALESCE(kw.kscore, 0) AS kscore
FROM kb_chunks c JOIN kb_documents d ON d.id = c.doc_id
LEFT JOIN vec ON vec.id = c.id LEFT JOIN kw ON kw.id = c.id
WHERE vec.id IS NOT NULL OR kw.id IS NOT NULL
"""


async def hybrid_search(query: str, sources: tuple[str, ...], k: int = 5) -> list[dict[str, Any]]:
    cfg = load_config("rag")
    raw = await get_redis().get("rag:version")
    version = raw.decode() if isinstance(raw, bytes) else (raw or "0")
    key = "cache:rag:" + hashlib.sha1(  # noqa: S324 - 캐시 키
        f"{version}|{query}|{','.join(sources)}|{k}".encode()).hexdigest()
    if (cached := await cache_get(key)) is not None:
        return list(cached)
    [emb] = await embed([query])
    async with session_scope() as db:
        rows = (await db.execute(text(_SQL), {
            "emb": json.dumps(emb), "sources": list(sources), "tsq": tsquery(query), "n": int(cfg["candidates"]),
        })).mappings().all()
    if not rows:
        return []
    w = cfg["weights"][provider()]
    kmax = max(r["kscore"] for r in rows) or 1.0
    vmax = max(r["vscore"] for r in rows) or 1.0
    scored = []
    for r in rows:
        score = w["vector"] * max(r["vscore"], 0) / vmax + w["keyword"] * r["kscore"] / kmax
        scored.append({"chunk_id": str(r["id"]), "doc_id": str(r["doc_id"]), "doc_title": r["title"],
                       "section": r["section"], "text": r["text"], "score": round(float(score), 4),
                       "ref_code": r["ref_code"], "source_type": r["source_type"]})
    scored.sort(key=lambda x: x["score"], reverse=True)
    out = scored[:k]
    await cache_set(key, out, int(cfg["cache_ttl_s"]))
    return out
