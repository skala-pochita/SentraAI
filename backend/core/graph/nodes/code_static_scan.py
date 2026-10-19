"""code_static_scan: 규칙 엔진 실행 (결과 캐시) → 정적 후보 (verdict=NEEDS_REVIEW). 등록된 오탐 규칙은 제외."""

from __future__ import annotations

import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig

from core.graph.deps import get_deps
from core.graph.nodes._base import node
from core.schemas import Evidence, FindingDraft, finding_fingerprint


def hit_to_draft(target_id: str, h: dict[str, Any]) -> FindingDraft:
    return FindingDraft(
        temp_id=f"s-{uuid.uuid4().hex[:8]}",
        target_id=target_id,
        source="code",
        category=h["category"],
        rule_id=h["rule_id"],
        title=f"{h['category']} at {h['path']}:{h['line']}",
        severity=h["severity"],
        confidence=0.6 if h.get("needs_llm_review") else 0.85,
        evidence=[
            Evidence(
                kind="code",
                file_path=h["path"],
                line_start=h["line"],
                line_end=h["line"],
                snippet_masked=h["snippet_masked"],
            )
        ],
        reasoning_summary=f"정적 규칙 {h['rule_id']} 이(가) 탐지했다.",
        tools_used=["run_static_scan"],
        verdict="NEEDS_REVIEW",
        standard_refs=list(h.get("references", [])),
        fix_hint=h.get("fix_hint"),
    )


def suppressed(f: FindingDraft, fp_rules: list[dict[str, Any]]) -> dict[str, Any] | None:
    fp = finding_fingerprint(f)
    for r in fp_rules:
        if r.get("fingerprint") and r["fingerprint"] == fp:
            return r
        if r.get("rule_id") and not r.get("fingerprint") and r["rule_id"] == f.rule_id:
            return r
    return None


@node("code_static_scan")
async def code_static_scan(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    target = state["target"]
    tid = target["target_id"]
    res = await deps.call(state, "code_static_scan", "run_static_scan", target_id=tid)
    fp_rules = await deps.call(state, "code_static_scan", "list_false_positive_rules", target_id=tid)
    candidates: list[dict[str, Any]] = []
    dropped = 0
    for h in res["hits"]:
        d = hit_to_draft(tid, h)
        if suppressed(d, fp_rules):
            dropped += 1
            continue
        candidates.append(d.model_dump())
    await deps.event(
        state,
        "scan.progress",
        {
            "target": target["name"],
            "hits": len(res["hits"]),
            "suppressed_by_fp_rules": dropped,
            "cached": res["cached"],
            "commit_sha": res["commit_sha"],
        },
        node="code_static_scan",
    )
    return {"candidates": candidates}
