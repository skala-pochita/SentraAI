"""지식 문서 청크·인덱싱 (명세 9.4: 마크다운 헤더 기준, 최대 800 토큰, overlap 100)."""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.models import KB_SOURCE_TYPES, KbChunk, KbDocument
from core.embeddings import embed
from core.errors import AppError, ErrorCode
from core.redis.client import get_redis

log = logging.getLogger("rag.ingest")

MAX_TOKENS = 800
OVERLAP_TOKENS = 100
_HEADER = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)


def count_tokens(text: str) -> int:
    """근사 토큰 수: ASCII 4자 ≈ 1토큰, 한글 등 비ASCII 1자 ≈ 1토큰."""
    ascii_n = sum(1 for c in text if c.isascii())
    return ascii_n // 4 + (len(text) - ascii_n)


@dataclass
class Chunk:
    section: str
    text: str


def _pieces(para: str) -> list[str]:
    """한 문단이 한도를 넘으면 공백 단위로 자른다."""
    if count_tokens(para) <= MAX_TOKENS - OVERLAP_TOKENS:
        return [para]
    out: list[str] = []
    cur = ""
    for word in para.split(" "):
        if cur and count_tokens(cur + " " + word) > MAX_TOKENS - OVERLAP_TOKENS:
            out.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}" if cur else word
    if cur:
        out.append(cur)
    return out


def _split_long(section: str, body: str) -> list[Chunk]:
    if count_tokens(body) <= MAX_TOKENS:
        return [Chunk(section, body)]
    paras = [x for p in re.split(r"\n\s*\n", body) if p.strip() for x in _pieces(p)]
    chunks: list[Chunk] = []
    cur: list[str] = []
    for p in paras:
        if cur and count_tokens("\n\n".join([*cur, p])) > MAX_TOKENS:
            chunks.append(Chunk(section, "\n\n".join(cur)))
            # overlap: 직전 청크 끝부분을 다음 청크 앞에 붙인다
            tail = "\n\n".join(cur)
            keep = ""
            for ch in reversed(tail):
                if count_tokens(keep) >= OVERLAP_TOKENS:
                    break
                keep = ch + keep
            cur = [keep]
        cur.append(p)
    if cur:
        chunks.append(Chunk(section, "\n\n".join(cur)))
    return chunks


def chunk_markdown(title: str, content: str) -> list[Chunk]:
    matches = list(_HEADER.finditer(content))
    if not matches:
        return _split_long(title, content.strip())
    out: list[Chunk] = []
    pre = content[: matches[0].start()].strip()
    if pre:
        out.extend(_split_long(title, pre))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        body = content[m.start() : end].strip()
        if body:
            # 섹션 제목을 본문에 포함 (검색 매칭용)
            out.extend(_split_long(m.group(2).strip(), f"[{title}] {body}"))
    return out


def parse_front_matter(md: str) -> tuple[dict[str, Any], str]:
    if md.startswith("---"):
        end = md.find("\n---", 3)
        if end > 0:
            meta = yaml.safe_load(md[3:end]) or {}
            return meta, md[end + 4 :].lstrip("\n")
    return {}, md


async def bump_rag_version() -> None:
    await get_redis().incr("rag:version")


async def index_document(db: AsyncSession, doc: KbDocument) -> int:
    await db.execute(delete(KbChunk).where(KbChunk.doc_id == doc.id))
    chunks = chunk_markdown(doc.title, doc.content_md)
    vectors = await embed([c.text for c in chunks])
    db.add_all(
        KbChunk(doc_id=doc.id, section=c.section, text=c.text, embedding=v)
        for c, v in zip(chunks, vectors, strict=True)
    )
    await db.flush()
    return len(chunks)


async def upsert_document(
    db: AsyncSession,
    title: str,
    source_type: str,
    content_md: str,
    ref_code: str | None = None,
    doc_id: uuid.UUID | None = None,
) -> tuple[KbDocument, int]:
    if source_type in {"kr_law", "kr_guideline"}:
        raise AppError(ErrorCode.VALIDATION, "use verified clause import/index for regulatory originals")
    if source_type not in KB_SOURCE_TYPES:
        raise AppError(ErrorCode.VALIDATION, f"source_type must be one of {KB_SOURCE_TYPES}")
    if not content_md.strip():
        raise AppError(ErrorCode.VALIDATION, "content_md is empty")
    doc = (
        await db.get(KbDocument, doc_id)
        if doc_id
        else (await db.execute(select(KbDocument).where(KbDocument.title == title))).scalar_one_or_none()
    )
    if doc is None:
        doc = KbDocument(title=title, source_type=source_type, content_md=content_md, ref_code=ref_code)
        db.add(doc)
        await db.flush()
    else:
        doc.title, doc.source_type, doc.content_md, doc.ref_code = title, source_type, content_md, ref_code
    n = await index_document(db, doc)
    return doc, n


async def ingest_dir(db: AsyncSession, directory: Path) -> list[tuple[str, int]]:
    out = []
    for path in sorted(directory.glob("*.md")):
        meta, body = parse_front_matter(path.read_text(encoding="utf-8"))
        doc, n = await upsert_document(
            db, meta.get("title", path.stem), meta.get("source_type", "policy"), body, meta.get("ref_code")
        )
        out.append((doc.title, n))
        log.info("indexed %s chunks=%d", path.name, n)
    await bump_rag_version()
    return out
