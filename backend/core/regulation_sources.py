"""Local original documents, deterministic clause boundaries, and provenance validation.

No network acquisition or inferred legal metadata. User imports remain outside Git.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from core.errors import AppError, ErrorCode
from core.settings import get_settings

CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳㉑㉒㉓㉔㉕㉖㉗㉘㉙㉚㉛㉜㉝㉞㉟㊱㊲㊳㊴㊵"
HASH = r"^[a-f0-9]{64}$"


def sha256(data: bytes | str) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def canonical_hash(value: Any) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def compact(text: str) -> str:
    # Only whitespace is ignored; punctuation, ellipses and words are not rewritten.
    return re.sub(r"\s+", "", text)


def local_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise AppError(ErrorCode.VALIDATION, "source path escapes source directory")
    return path


class Clause(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=200)
    source: str
    source_sha256: str = Field(pattern=HASH)
    file_sha256: str | None = Field(default=None, pattern=HASH)
    heading: str = Field(min_length=1)
    text: str = Field(min_length=1)
    condition: str = "conditional"
    assessable: bool = True
    evidence_scope: Literal["finding", "probe", "audit", "organization"] = "finding"
    source_type: Literal["kr_law", "kr_guideline"] = "kr_guideline"
    title: str
    issuer: str
    source_url: str | None = None
    version: str | None = None


def clean_text(text: str, title: str) -> str:
    lines = text.replace("\r\n", "\n").replace("\f", "\n").splitlines()
    return "\n".join(line for line in lines if line.strip() != title and not re.fullmatch(r"\s*-\s*\d+\s*-\s*", line))


def _blocks(text: str, pattern: str) -> list[tuple[re.Match[str], str]]:
    matches = list(re.finditer(pattern, text, re.MULTILINE))
    return [
        (m, text[m.start() : matches[i + 1].start() if i + 1 < len(matches) else len(text)].strip())
        for i, m in enumerate(matches)
    ]


def normalize_clauses(source: dict[str, Any], text: str) -> list[Clause]:
    """Split law article/paragraph/item, checklist principle/item, or headed sections.

    Unknown structure is rejected, rather than turning arbitrary PDF pages into clauses.
    """
    if source.get("format") == "notice" or not source.get("regulatory_evidence", True):
        return []
    cleaned = clean_text(text, source["title"])
    entries: list[tuple[str, str, str]] = []
    if source["format"] == "law":
        articles = _blocks(cleaned, r"^\s*제(\d+)조(?:의(\d+))?\s*\(([^\n)]+)\)")
        for match, block in articles:
            article = match[1] + (f"_{match[2]}" if match[2] else "")
            heading = match[0].strip()
            entries.append((article, heading, block))
            paragraphs = _blocks(block, rf"([{CIRCLED}])\s*")
            for para, body in paragraphs:
                pn = CIRCLED.index(para[1]) + 1
                entries.append((f"{article}-{pn}", heading, body))
                for item, words in _blocks(body, r"^\s*(\d+)\.\s+"):
                    entries.append((f"{article}-{pn}-{item[1]}", heading, words))
    elif source["format"] == "checklist":
        principles = _blocks(cleaned, r"^\s*\[?([1-7])\.\s*([^\n\]]*원칙)[^\n]*")
        for principle, block in principles:
            checks = _blocks(block, rf"^\s*([{CIRCLED}])\s*")
            for check, words in checks:
                number = CIRCLED.index(check[1]) + 1
                entries.append((f"{principle[1]}-CHK-{number:02}", principle[0].strip(), words))
    else:
        sections = _blocks(cleaned, r"^\s*(?:제\d+장[^\n]*|\d+(?:\.\d+)*\s+[^\n]+)")
        for i, (section, words) in enumerate(sections, 1):
            entries.append((f"SEC-{i:03}", section[0].strip(), words))
    if not entries:
        raise AppError(ErrorCode.VALIDATION, "no clause boundaries found; inspect extracted text and format")
    out: dict[str, Clause] = {}
    for key, heading, body in entries:
        cid = f"{source['id']}:{key}"
        record = Clause(
            id=cid,
            source=source["id"],
            source_sha256=sha256(text),
            file_sha256=source.get("file_sha256"),
            heading=heading,
            text=body,
            title=source["title"],
            issuer=source["issuer"],
            condition=source.get("condition", "conditional"),
            source_type=source["source_type"],
            source_url=source.get("source_url"),
            version=source.get("version"),
        )
        if cid in out and out[cid].text != body:
            raise AppError(ErrorCode.VALIDATION, f"ambiguous clause ID {cid}; remove table-of-contents duplicates")
        out[cid] = record
    return list(out.values())


def sources_root() -> Path:
    return get_settings().knowledge_dir / "sources"


def read_manifest(root: Path) -> dict[str, Any]:
    base = yaml.safe_load((root / "MANIFEST.yaml").read_text(encoding="utf-8"))
    overlay = root / "MANIFEST.local.yaml"
    if overlay.exists():
        local = yaml.safe_load(overlay.read_text(encoding="utf-8")) or {}
        updates = {s["id"]: s for s in local.get("sources", [])}
        base["sources"] = [{**s, **updates.pop(s["id"], {})} for s in base["sources"]] + list(updates.values())
    ids = [s["id"] for s in base["sources"]]
    if any(not re.fullmatch(r"[A-Z0-9][A-Z0-9_-]{0,79}", source_id) for source_id in ids):
        raise AppError(ErrorCode.VALIDATION, "invalid source ID")
    if len(ids) != len(set(ids)):
        raise AppError(ErrorCode.VALIDATION, "duplicate source IDs")
    return dict(base)


def load_clauses(root: Path | None = None) -> tuple[dict[str, Clause], list[dict[str, str]]]:
    root = root or sources_root()
    manifest = read_manifest(root)
    out: dict[str, Clause] = {}
    missing = []
    for source in manifest["sources"]:
        if source.get("status") != "AVAILABLE":
            missing.append({"source": source["id"], "title": source["title"], "reason": "원문 미확보"})
            continue
        original = local_path(root, source["raw_file"]).read_bytes()
        raw = local_path(root, source["text_file"]).read_bytes()
        if sha256(original) != source["file_sha256"] or sha256(raw) != source["text_sha256"]:
            raise AppError(ErrorCode.VALIDATION, f"source hash mismatch: {source['id']}")
        if source.get("format") == "notice" or not source.get("regulatory_evidence", True):
            continue
        content = compact(clean_text(raw.decode("utf-8"), source["title"]))
        records = yaml.safe_load(local_path(root, source["clauses_file"]).read_text(encoding="utf-8"))
        if records["source_sha256"] != source["text_sha256"]:
            raise AppError(ErrorCode.VALIDATION, f"clause source hash mismatch: {source['id']}")
        for record in records["clauses"]:
            c = Clause.model_validate(record)
            if (
                c.id in out
                or c.source != source["id"]
                or not c.id.startswith(c.source + ":")
                or c.source_sha256 != source["text_sha256"]
                or c.file_sha256 != source["file_sha256"]
                or not compact(c.text)
                or compact(c.text) not in content
            ):
                raise AppError(ErrorCode.VALIDATION, f"invalid clause provenance: {c.id}")
            # Citation metadata is taken from the pinned manifest, not editable clause fields.
            if (c.title, c.issuer, c.source_url, c.version, c.source_type) != (
                source["title"],
                source["issuer"],
                source.get("source_url"),
                source.get("version"),
                source["source_type"],
            ):
                raise AppError(ErrorCode.VALIDATION, f"clause metadata mismatch: {c.id}")
            out[c.id] = c
    return out, missing
