"""Offline source import, normalization, link estimation and optional human review.

python -m core.compliance_cli --help
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from core.compliance import Link, load_catalog, validate_links
from core.errors import AppError, ErrorCode
from core.regulation_sources import (
    canonical_hash,
    compact,
    load_clauses,
    normalize_clauses,
    read_manifest,
    sha256,
    sources_root,
)
from core.settings import get_settings, load_config
from harness.budget import Budget
from harness.context import wrap_untrusted
from harness.llm import llm_available, make_chat_model, model_name_for, structured_call
from harness.masking import mask_text
from harness.prompts import get_prompt


def atomic_yaml(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8")
    temp.replace(path)


def import_source(
    root: Path,
    source_id: str,
    file: Path,
    source_url: str | None = None,
    version: str | None = None,
    acquisition: dict[str, Any] | None = None,
) -> int:
    manifest = read_manifest(root)
    source = next((s.copy() for s in manifest["sources"] if s["id"] == source_id), None)
    if source is None:
        raise AppError(ErrorCode.VALIDATION, "source ID not registered in MANIFEST.yaml")
    data = file.read_bytes()
    if file.suffix.lower() == ".pdf":
        if not data.startswith(b"%PDF-"):
            raise AppError(ErrorCode.VALIDATION, "not a PDF")
        if not shutil.which("pdftotext"):
            raise AppError(ErrorCode.VALIDATION, "PDF extraction needs pdftotext (use backend container)")
        try:
            text = subprocess.run(
                ["pdftotext", "-layout", str(file.resolve()), "-"], capture_output=True, timeout=60, check=True
            ).stdout.decode("utf-8")
        except (subprocess.SubprocessError, UnicodeError) as exc:
            raise AppError(ErrorCode.VALIDATION, "PDF text extraction failed") from exc
    else:
        text = data.decode("utf-8")
    if not text.strip():
        raise AppError(ErrorCode.VALIDATION, "empty extracted text; scanned PDF needs OCR before import")
    if source_url:
        source["source_url"] = source_url
    if version:
        source["version"] = version
    # Preserve the precise acquisition URL and request method separately from the
    # human-facing publisher page. HTTPS retrieval does not prove authenticity.
    if acquisition:
        source["acquisition"] = acquisition
    # A supplied source is locally pinned, not labelled an authenticated official copy.
    source.update(
        status="AVAILABLE",
        kind="local",
        official_copy_verified=False,
        acquired_at=datetime.now(UTC).isoformat(),
        file_sha256=sha256(data),
        text_sha256=sha256(text),
    )
    clauses = normalize_clauses(source, text)
    stem = source_id.lower().replace("-", "_")
    source.update(
        raw_file=f"raw/{stem}{file.suffix.lower()}", text_file=f"text/{stem}.txt", clauses_file=f"clauses/{stem}.yaml"
    )
    pages = text.split("\f")
    if not pages[-1].strip():
        pages.pop()
    source["page_count"] = len(pages)
    source["markdown_file"] = f"markdown/{stem}.md"
    metadata = {
        key: source[key]
        for key in (
            "id",
            "title",
            "issuer",
            "format",
            "source_url",
            "version",
            "file_sha256",
            "text_sha256",
            "page_count",
        )
        if key in source
    }
    metadata["regulatory_evidence"] = source.get("regulatory_evidence", True)
    metadata["transcription"] = "pdftotext -layout" if file.suffix.lower() == ".pdf" else "UTF-8 text"
    metadata["acquisition"] = source.get("acquisition", {"method": "local_import"})
    md = "---\n" + yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False) + "---\n\n"
    md += f"# {source['title']}\n\n"
    if not metadata["regulatory_evidence"]:
        md += "> 참고 보도자료입니다. 규제 의무나 AI RMF 본문으로 사용하지 않습니다.\n\n"
    md += "> 원문 배치를 보존한 자동 추출본입니다. 표·도식·추출 누락 여부는 원본 PDF를 대조합니다.\n\n"
    for number, page in enumerate(pages, 1):
        # Literal blocks retain table columns and avoid executing embedded Markdown/HTML.
        fence = "`" * max(3, max((len(m[0]) + 1 for m in re.finditer(r"`+", page)), default=3))
        md += f"## PDF 페이지 {number}\n\n{fence}text\n{page.rstrip()}\n{fence}\n\n"
    source["markdown_sha256"] = sha256(md)
    for folder in ("raw", "text", "clauses", "markdown"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    (root / source["raw_file"]).write_bytes(data)
    (root / source["text_file"]).write_text(text, encoding="utf-8")
    (root / source["markdown_file"]).write_text(md, encoding="utf-8")
    atomic_yaml(
        root / source["clauses_file"],
        {"source": source_id, "source_sha256": source["text_sha256"], "clauses": [c.model_dump() for c in clauses]},
    )
    available = [s for s in manifest["sources"] if s.get("status") == "AVAILABLE" and s["id"] != source_id]
    atomic_yaml(root / "MANIFEST.local.yaml", {"schema": 1, "sources": [*available, source]})
    return len(clauses)


def merge_links(existing: list[Link], generated: list[Link]) -> list[Link]:
    merged = {link.identity: link for link in existing}
    for link in generated:
        previous = merged.get(link.identity)
        if previous is None or previous.relevance == "ai_estimated":
            merged[link.identity] = link
    return sorted(merged.values(), key=lambda link: link.identity)


class RelevanceDecision(BaseModel):
    relevant: bool
    reason: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    quote: str


async def generate_links(root: Path | None = None) -> dict[str, Any]:
    """Retrieve candidates in RAG, then re-fetch exact clauses; write only after a successful run."""
    from mcp_servers.rag.regulations import exact_clause, search_clauses

    root = root or sources_root()
    clauses, _ = load_clauses(root)
    if not clauses:
        raise AppError(ErrorCode.VALIDATION, "no original clauses; import sources and index first")
    if not llm_available():
        raise AppError(ErrorCode.VALIDATION, "OPENAI_API_KEY required; no synthetic relevance results are generated")
    cfg = load_config("compliance/link")
    budget = Budget(int(cfg["max_tokens"]), float(cfg["max_cost_usd"]))
    model_name = model_name_for("compliance_link")
    model, prompt = make_chat_model(model_name), get_prompt("compliance_link@v1")
    usage: list[dict[str, Any]] = []
    generated: list[Link] = []
    pairs = 0
    rule_inputs = [
        yaml.safe_load(rule_path.read_text(encoding="utf-8"))
        for rule_path in sorted(get_settings().rules_dir.glob("**/*.yaml"))
    ]
    rule_inputs.extend(cfg.get("extra_queries", []))
    for rule in rule_inputs:
        if not rule.get("category"):
            continue
        query = " ".join(str(rule.get(k, "")) for k in ("name", "title", "description", "category", "fix_hint"))
        candidates = await search_clauses(query, None, int(cfg["candidates"]))
        for candidate in candidates:
            if pairs >= int(cfg["max_pairs"]) or budget.exceeded():
                raise AppError(ErrorCode.BUDGET_EXCEEDED, "link budget exhausted; previous links unchanged")
            fetched = await exact_clause(candidate["clause_id"])
            clause = clauses.get(fetched["id"])
            if clause is None or fetched != clause.model_dump():
                raise AppError(ErrorCode.VALIDATION, "RAG/source snapshot mismatch; reindex first")
            decision = await structured_call(
                model,
                prompt,
                RelevanceDecision,
                node="compliance_link",
                model_name=model_name,
                budget=budget,
                usage=usage,
                rule=wrap_untrusted(json.dumps(rule, ensure_ascii=False), "security_rule"),
                clause=wrap_untrusted(clause.text, "regulation_clause"),
            )
            pairs += 1
            if not decision.relevant or decision.confidence < float(cfg["confidence_threshold"]):
                continue
            if not compact(decision.quote) or compact(decision.quote) not in compact(clause.text):
                raise AppError(ErrorCode.VALIDATION, "LLM quotation differs from original; previous links unchanged")
            metadata = {
                "model": model_name,
                "prompt": prompt.ref,
                "at": datetime.now(UTC).isoformat(),
                "confidence": decision.confidence,
                "source_sha256": clause.source_sha256,
            }
            # Rule-specific links plus category fallbacks cover adaptive/ReAct findings without rule IDs.
            keys = [{"category": rule["category"]}]
            if rule.get("id"):
                keys.insert(0, {"rule_id": rule["id"]})
            for key in keys:
                generated.append(
                    Link(
                        key=key,
                        clause=clause.id,
                        quote=decision.quote,
                        quote_sha256=sha256(decision.quote),
                        source_sha256=clause.source_sha256,
                        reason=mask_text(decision.reason),
                        generated_by=metadata,
                    )
                )
    path = get_settings().compliance_config_dir or get_settings().config_dir / "compliance"
    catalog = load_catalog(path, root)
    merged = merge_links(catalog.links, generated)
    validate_links(merged, clauses)
    # Consolidate only into generated.yaml; other curated files must not duplicate generated identities.
    generated_path = path / "links/generated.yaml"
    other_ids: set[str] = set()
    for file in sorted((path / "links").glob("*.yaml")):
        if file != generated_path:
            other_ids.update(
                Link.model_validate(item).identity for item in (yaml.safe_load(file.read_text(encoding="utf-8")) or [])
            )
    atomic_yaml(generated_path, [link.model_dump() for link in merged if link.identity not in other_ids])
    return {"pairs": pairs, "generated": len(generated), "usage": usage, "budget": budget.snapshot()}


def bootstrap_links(source_root: Path | None = None) -> dict[str, int]:
    """Design candidates are explicitly labelled as prior estimates, never as model measurements."""
    root = source_root or sources_root()
    path = get_settings().compliance_config_dir or get_settings().config_dir / "compliance"
    catalog = load_catalog(path, root)
    candidates = yaml.safe_load((path / "initial_candidates.yaml").read_text(encoding="utf-8")) or []
    generated = []
    for candidate in candidates:
        matches = [
            c
            for c in catalog.clauses.values()
            if c.source == candidate["source"]
            and compact(candidate["quote"]) in compact(c.text)
            and (not candidate.get("clause") or c.id == candidate["clause"])
        ]
        if matches and not candidate.get("clause"):
            shortest = min(len(compact(c.text)) for c in matches)
            matches = [c for c in matches if len(compact(c.text)) == shortest]
        if len(matches) != 1:
            continue
        generated.append(
            Link(
                key=candidate["key"],
                clause=matches[0].id,
                quote=candidate["quote"],
                quote_sha256=sha256(candidate["quote"]),
                source_sha256=matches[0].source_sha256,
                reason="이전 AI 설계의 초기 관련성 추정. 실제 모델 재평가 전.",
                generated_by={
                    "model": "prior_design_estimate",
                    "prompt": "user_supplied_design_v2",
                    "at": datetime.now(UTC).isoformat(),
                },
            )
        )
    merged = merge_links(catalog.links, generated)
    validate_links(merged, catalog.clauses)
    target = path / "links/generated.yaml"
    others = {link.identity for link in catalog.links}
    if target.exists():
        others -= {Link.model_validate(x).identity for x in (yaml.safe_load(target.read_text()) or [])}
    atomic_yaml(target, [link.model_dump() for link in merged if link.identity not in others])
    return {
        "candidates": len(candidates),
        "verified_links": len(generated),
        "unavailable": len(candidates) - len(generated),
    }


def review_link(link_id: str, decision: str, reviewer: str) -> None:
    if not reviewer.strip():
        raise AppError(ErrorCode.VALIDATION, "reviewer required")
    catalog = load_catalog()
    if not any(link.identity == link_id for link in catalog.links):
        raise AppError(ErrorCode.NOT_FOUND, "link ID not found")
    for file in sorted(
        ((get_settings().compliance_config_dir or get_settings().config_dir / "compliance") / "links").glob("*.yaml")
    ):
        items = yaml.safe_load(file.read_text(encoding="utf-8")) or []
        for item in items:
            if canonical_hash([item["key"], item["clause"]]) == link_id:
                item.update(relevance=decision, confirmed_by=reviewer, confirmed_at=datetime.now(UTC).isoformat())
                Link.model_validate(item)
                atomic_yaml(file, items)
                return


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    imp = commands.add_parser("import")
    imp.add_argument("source_id")
    imp.add_argument("file", type=Path)
    imp.add_argument("--source-url")
    imp.add_argument("--version")
    for name in ("validate", "index", "link", "bootstrap"):
        commands.add_parser(name)
    download = commands.add_parser(
        "download", help="download registered public originals, transcribe Markdown, normalize"
    )
    download.add_argument("source_ids", nargs="*")
    search = commands.add_parser("search", help="retrieve indexed regulatory text")
    search.add_argument("query")
    search.add_argument("--source")
    search.add_argument("-k", type=int, default=5)
    review = commands.add_parser("review")
    review.add_argument("link_id")
    review.add_argument("decision", choices=["human_confirmed", "rejected"])
    review.add_argument("--reviewer", required=True)
    args = parser.parse_args()
    try:
        if args.command == "import":
            result: Any = {
                "clauses": import_source(sources_root(), args.source_id, args.file, args.source_url, args.version)
            }
        elif args.command == "validate":
            catalog = load_catalog()
            result = {
                "clauses": len(catalog.clauses),
                "links": len(catalog.links),
                "missing": catalog.missing_sources,
                "digest": catalog.digest,
            }
        elif args.command == "index":
            from mcp_servers.rag.regulations import ingest_regulations

            result = asyncio.run(ingest_regulations())
        elif args.command == "link":
            result = asyncio.run(generate_links())
        elif args.command == "bootstrap":
            result = bootstrap_links()
        elif args.command == "download":
            from core.regulation_download import download_sources

            result = download_sources(args.source_ids)
        elif args.command == "search":
            from mcp_servers.rag.regulations import search_clauses

            result = asyncio.run(search_clauses(args.query, args.source, args.k))
        else:
            review_link(args.link_id, args.decision, args.reviewer)
            result = {"reviewed": args.link_id}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (AppError, OSError, ValueError, yaml.YAMLError) as exc:
        message = exc.message if isinstance(exc, AppError) else type(exc).__name__
        parser.exit(2, mask_text(message) + "\n")


if __name__ == "__main__":
    main()
