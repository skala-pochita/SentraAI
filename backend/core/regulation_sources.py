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
    content_kind: Literal["requirement", "recommendation", "example", "toc", "definition", "addendum", "unknown"] = (
        "unknown"
    )
    classification_basis: Literal["unreviewed", "structure", "curated_excerpt"] = "unreviewed"
    parent_id: str | None = None
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)


def evidence_candidate(clause: Clause) -> bool:
    """A searchable candidate is still not a relevance or applicability decision."""
    return clause.content_kind in {"requirement", "recommendation"} and clause.classification_basis != "unreviewed"


def classify_clauses(root: Path, clauses: dict[str, Clause], *, include_policy: bool = True) -> dict[str, Clause]:
    # Legacy page chunks are unclassified. Only genuine checklist boundaries get
    # structural classification; local YAML flags alone cannot promote a page.
    out = {}
    for cid, original in clauses.items():
        checklist = bool(re.fullmatch(r"[^:]+:[1-7]-CHK-\d+", cid))
        out[cid] = original.model_copy(
            update={
                "content_kind": "recommendation" if checklist else "addendum" if ":ADDENDUM-" in cid else "unknown",
                "classification_basis": "structure" if checklist else "unreviewed",
                "parent_id": None,
                "assessable": original.assessable if checklist else False,
            }
        )
    policy = root / "evidence_policy.yaml"
    if not include_policy or not policy.exists():
        return out
    records = yaml.safe_load(policy.read_text(encoding="utf-8")) or {}
    for item in records.get("classifications", []):
        clause = out.get(item["clause"])
        if clause is None:
            continue
        if clause.source_sha256 != item["source_sha256"] or not evidence_candidate(clause):
            raise AppError(ErrorCode.VALIDATION, "stale checklist classification")
        scope = item["evidence_scope"]
        out[clause.id] = Clause.model_validate(
            {
                **clause.model_dump(),
                "evidence_scope": scope,
                "assessable": scope != "organization",
                "condition": item.get("condition", clause.condition),
            }
        )
    for item in records.get("excerpts", []):
        parent = out.get(item["parent_id"])
        if parent is None:
            # A checkout without local originals must remain usable.
            if any(c.source == item["parent_id"].split(":")[0] for c in out.values()):
                raise AppError(ErrorCode.VALIDATION, "evidence policy parent missing")
            continue
        text = item["text"]
        cid = parent.id + ":EVIDENCE-" + item["slug"]
        if (
            item["source_sha256"] != parent.source_sha256
            or not compact(text)
            or compact(text) not in compact(parent.text)
            or cid in out
            or item["content_kind"] not in {"requirement", "recommendation"}
        ):
            raise AppError(ErrorCode.VALIDATION, "stale or invalid evidence policy excerpt")
        out[cid] = Clause.model_validate(
            {
                **parent.model_dump(),
                "id": cid,
                "parent_id": parent.id,
                "heading": item["heading"],
                "text": text,
                "content_kind": item["content_kind"],
                "classification_basis": "curated_excerpt",
                "condition": item.get("condition", parent.condition),
                "evidence_scope": item.get("evidence_scope", "finding"),
                "assessable": item.get("evidence_scope", "finding") != "organization",
            }
        )
    return out


def clean_text(text: str, title: str) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0e-\x1f]", "", text)
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
            # Narrative examples also use circled numbers. Only the actual checklists
            # supply CHK identifiers; the next principle heading closes each checklist.
            if not re.search(r"점검\s*항목", principle[0]):
                continue
            checks = _blocks(block, rf"^\s*([{CIRCLED}])\s*")
            for check, words in checks:
                number = CIRCLED.index(check[1]) + 1
                entries.append((f"{principle[1]}-CHK-{number:02}", principle[0].strip(), words))
    else:
        # A PDF page is a physical locator, never an invented legal clause number.
        # Keep the complete transcription (including tables) in Markdown; bounded
        # page segments are retrieval candidates with explicit physical page IDs.
        for page, original in enumerate(text.split("\f"), 1):
            body = clean_text(original, source["title"]).strip()
            if not body or not re.search(r"[가-힣A-Za-z]", body):
                continue
            parts: list[str] = []
            current = ""
            for line in body.splitlines(keepends=True):
                if current and len(current) + len(line) > 2200:
                    parts.append(current.strip())
                    current = ""
                current += line
            if current.strip():
                parts.append(current.strip())
            for segment, words in enumerate(parts, 1):
                heading = next((line.strip() for line in words.splitlines() if line.strip()), source["title"])
                entries.append((f"PAGE-{page:03}-{segment:02}", f"PDF p.{page} · {heading[:100]}", words))
    if not entries:
        raise AppError(ErrorCode.VALIDATION, "no clause boundaries found; inspect extracted text and format")
    out: dict[str, Clause] = {}
    page_texts = [compact(clean_text(page, source["title"])) for page in text.split("\f")]
    page_ends: list[int] = []
    for page_text in page_texts:
        page_ends.append((page_ends[-1] if page_ends else 0) + len(page_text))
    full = "".join(page_texts)
    for key, heading, body in entries:
        cid = f"{source['id']}:{key}"
        start = full.find(compact(body))
        first = next((i + 1 for i, end in enumerate(page_ends) if end > start), None) if start >= 0 else None
        last_pos = start + len(compact(body)) - 1
        last = next((i + 1 for i, end in enumerate(page_ends) if end > last_pos), None) if start >= 0 else None
        if key.startswith("PAGE-"):
            first = last = int(key.split("-")[1])
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
            page_start=first,
            page_end=last,
            content_kind="recommendation" if source["format"] == "checklist" else "unknown",
            classification_basis="structure" if source["format"] == "checklist" else "unreviewed",
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


def load_clauses(
    root: Path | None = None, *, include_policy: bool = True
) -> tuple[dict[str, Clause], list[dict[str, str]]]:
    root = root or sources_root()
    manifest = read_manifest(root)
    out: dict[str, Clause] = {}
    missing = []
    for source in manifest["sources"]:
        scope = source.get("scope_role", "evidence" if source.get("regulatory_evidence", True) else "reference")
        if scope not in {"evidence", "reference", "excluded"}:
            raise AppError(ErrorCode.VALIDATION, "invalid source scope_role")
        if scope == "excluded":
            continue
        if source.get("status") != "AVAILABLE":
            if scope == "evidence":
                missing.append({"source": source["id"], "title": source["title"], "reason": "원문 미확보"})
            continue
        original = local_path(root, source["raw_file"]).read_bytes()
        raw = local_path(root, source["text_file"]).read_bytes()
        if sha256(original) != source["file_sha256"] or sha256(raw) != source["text_sha256"]:
            raise AppError(ErrorCode.VALIDATION, f"source hash mismatch: {source['id']}")
        if (
            source.get("markdown_file")
            and sha256(local_path(root, source["markdown_file"]).read_bytes()) != source["markdown_sha256"]
        ):
            raise AppError(ErrorCode.VALIDATION, f"Markdown hash mismatch: {source['id']}")
        if scope == "reference" or source.get("format") == "notice" or not source.get("regulatory_evidence", True):
            continue
        content = compact(clean_text(raw.decode("utf-8"), source["title"]))
        records = yaml.safe_load(local_path(root, source["clauses_file"]).read_text(encoding="utf-8"))
        if records["source_sha256"] != source["text_sha256"]:
            raise AppError(ErrorCode.VALIDATION, f"clause source hash mismatch: {source['id']}")
        for record in records["clauses"]:
            c = Clause.model_validate(record)
            pages = raw.decode("utf-8").split("\f")
            if c.page_start is not None and (
                c.page_end is None
                or not c.page_start <= c.page_end <= len(pages)
                or compact(c.text)
                not in compact(clean_text("\f".join(pages[c.page_start - 1 : c.page_end]), source["title"]))
            ):
                raise AppError(ErrorCode.VALIDATION, f"clause page locator mismatch: {c.id}")
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
            if source.get("default_evidence_scope"):
                scope = source["default_evidence_scope"]
                c = Clause.model_validate(
                    {**c.model_dump(), "evidence_scope": scope, "assessable": scope != "organization"}
                )
            out[c.id] = c
    return classify_clauses(root, out, include_policy=include_policy), missing
