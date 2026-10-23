#!/usr/bin/env python3
"""Dependency-free reader for the delivered local corpus. No API calls or generated answers."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "knowledge/sources"


def verify() -> dict:
    inventory = json.loads(
        (SOURCES / "index/corpus_inventory.json").read_text(encoding="utf-8")
    )
    for item in inventory["files"]:
        path = (SOURCES / item["path"]).resolve()
        if (
            not path.is_relative_to(SOURCES.resolve())
            or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]
        ):
            raise ValueError("원문/Markdown/인덱스 해시 불일치: " + item["path"])
    return inventory


def search(query: str, source: str | None = None, k: int = 5) -> list[dict]:
    # This zero-install viewer ranks keywords. Full hash/OpenAI hybrid retrieval
    # remains available through backend core.compliance_cli search.
    verify()
    tokens = set(re.findall(r"[a-zA-Z0-9_]+|[가-힣]{2,}", query.lower()))
    if not tokens:
        return []
    db_path = SOURCES / "index/regulations.sqlite3"
    with sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True) as db:
        rows = db.execute(
            "SELECT data FROM clauses WHERE (? IS NULL OR source = ?)", (source, source)
        ).fetchall()
    results = []
    for row in rows:
        clause = json.loads(row[0])
        if (
            clause.get("content_kind") not in {"requirement", "recommendation"}
            or clause.get("classification_basis") == "unreviewed"
        ):
            continue
        text = clause["text"].lower()
        matched = sum(token in text for token in tokens)
        if matched:
            # Prefer complete term coverage and focused passages over long pages.
            score = matched / len(tokens) + sum(
                min(text.count(token), 3) for token in tokens
            ) / max(100, len(text))
            results.append({**clause, "score": score, "candidate_only": True})
    return sorted(results, key=lambda c: (-c["score"], c["id"]))[:k]


def show(query: str, source: str | None, k: int) -> None:
    results = search(query, source, k)
    if not results:
        print("일치하는 검색어가 없습니다. 다른 표현으로 검색해 주세요.")
    for number, clause in enumerate(results, 1):
        print(
            f"\n{number}. {clause['title']} | {clause['id']} | PDF p.{clause.get('page_start')}"
        )
        print(f"출처: {clause.get('source_url') or clause['issuer']}")
        print(clause["text"].strip())
        print("-" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?")
    parser.add_argument("--source")
    parser.add_argument("-k", type=int, default=3)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    inventory = verify()
    evidence_count = inventory.get("evidence_source_count", len(inventory["sources"]))
    reference_count = inventory.get("reference_source_count", 0)
    print(
        f"원문 검증 완료: 근거 문서 {evidence_count}종 · 참고 {reference_count}종 · 전체 저장 단위 {inventory['documents']}개"
    )
    if args.verify:
        return
    if args.query:
        show(args.query, args.source, args.k)
        return
    print("로컬 원문 키워드 검색입니다. 모델 답변·준수 판정을 생성하지 않습니다.")
    print("예: 프롬프트 인젝션 / 입출력 필터링 / 위험관리방안 (종료: q)")
    while True:
        try:
            query = input("\n검색어 > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if query.lower() in {"q", "quit", "exit"}:
            return
        if query:
            show(query, args.source, args.k)


if __name__ == "__main__":
    main()
