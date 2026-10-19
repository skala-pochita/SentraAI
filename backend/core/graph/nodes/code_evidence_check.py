"""code_evidence_check: 근거 검증 가드레일 (H4). evidence 의 파일·라인을 read_repo_file 로 다시 읽어
실제 존재·일치를 확인하고, 불일치는 confidence 하향 + NEEDS_REVIEW."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from core.errors import AppError
from core.graph.deps import get_deps
from core.graph.nodes._base import node
from harness.guardrails import evidence_matches


@node("code_evidence_check")
async def code_evidence_check(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    tid = state["target"]["target_id"]
    checked, failed = [], 0
    cache: dict[str, str] = {}
    for f in state.get("investigated", []):
        if f.get("verdict") == "FALSE_POSITIVE":
            checked.append(f)
            continue
        ok = True
        for ev in f.get("evidence", []):
            if ev.get("kind") != "code" or not ev.get("file_path"):
                continue
            path = ev["file_path"]
            if path not in cache:
                try:
                    res = await deps.call(state, "code_evidence_check", "read_repo_file", target_id=tid, path=path)
                    cache[path] = res["content_masked"]
                except AppError:
                    cache[path] = ""
            if not evidence_matches(cache[path], ev):
                ok = False
        if not ok:
            failed += 1
            f = {
                **f,
                "confidence": round(max(0.1, float(f["confidence"]) - 0.3), 2),
                "verdict": "NEEDS_REVIEW",
                "reasoning_summary": f["reasoning_summary"] + " (근거 검증 실패: 파일·라인 불일치)",
            }
        checked.append(f)
    await deps.event(
        state, "guardrail.result", {"checked": len(checked), "evidence_mismatch": failed}, node="code_evidence_check"
    )
    return {"code_findings": checked}
