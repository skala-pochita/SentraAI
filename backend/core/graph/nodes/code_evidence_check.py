"""code_evidence_check: 근거 검증 가드레일 (H4). evidence 의 파일·라인을 read_repo_file 로 다시 읽어
실제 존재·일치를 확인하고, 불일치는 confidence 하향 + NEEDS_REVIEW."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from core.errors import AppError
from core.graph.deps import get_deps
from core.graph.nodes._base import node
from harness.guardrails import evidence_matches, relocate_evidence

READ_CHUNK_LINES = 400  # read_repo_file 1회 최대 라인 수 (MAX_READ_LINES)
MAX_READ_CHUNKS = 50  # 파일당 최대 20,000줄까지 검증


async def read_full_file(deps: Any, state: dict[str, Any], tid: str, path: str) -> str:
    """read_repo_file 은 1회 400줄까지만 돌려준다. 근거가 400줄 뒤에 있어도 검증되도록 끝까지 나눠 읽는다."""
    parts: list[str] = []
    start = 1
    for _ in range(MAX_READ_CHUNKS):
        res = await deps.call(
            state,
            "code_evidence_check",
            "read_repo_file",
            target_id=tid,
            path=path,
            start_line=start,
            end_line=start + READ_CHUNK_LINES - 1,
        )
        if res.get("content_masked"):
            parts.append(res["content_masked"])
        end, total = int(res.get("end_line") or 0), int(res.get("total_lines") or 0)
        if end >= total or end < start:
            break
        start = end + 1
    return "\n".join(parts)


@node("code_evidence_check")
async def code_evidence_check(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    tid = state["target"]["target_id"]
    checked, failed, relocated = [], 0, 0
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
                    cache[path] = await read_full_file(deps, state, tid, path)
                except AppError:
                    cache[path] = ""
            if not evidence_matches(cache[path], ev):
                fixed = relocate_evidence(cache[path], ev)
                if fixed is not None:  # 근거 라인 보정 (모델이 라인 번호만 틀린 경우)
                    span = int(ev.get("line_end") or ev.get("line_start") or fixed) - int(ev.get("line_start") or fixed)
                    ev["line_start"], ev["line_end"] = fixed, fixed + max(0, span)
                    relocated += 1
                else:
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
        state,
        "guardrail.result",
        {"checked": len(checked), "evidence_mismatch": failed, "relocated": relocated},
        node="code_evidence_check",
    )
    return {"code_findings": checked}
