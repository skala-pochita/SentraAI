"""ReAct 조사 (명세 8.5 (1); DECISIONS #13).

정적 후보가 있는 파일 + 위험 파일 휴리스틱 상위 K 개를 파일마다 최대 `react.max_steps`(6) 스텝으로 조사한다.
파일 원문은 처음부터 주지 않고 에이전트가 read_repo_file 로 필요한 범위를 읽는다(도구 선택 증거).
모든 도구 호출은 H3 게이트를 거치며 investigate.step 이벤트로 {tool, args_summary, why} 가 나간다.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Literal

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from core.errors import AppError, ErrorCode
from core.graph.deps import Deps, LLMFactory, get_deps
from core.graph.nodes.code_investigate_static import static_verdicts
from core.schemas import Evidence, FindingDraft, Severity
from harness.budget import limits
from harness.context import estimate_tokens, wrap_untrusted
from harness.llm import structured_call
from harness.prompts import get_prompt
from harness.tools import read_only_tools_for


class Action(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class Verdict(BaseModel):
    temp_id: str
    verdict: Literal["TRUE_POSITIVE", "FALSE_POSITIVE", "NEEDS_REVIEW"]
    confidence: float = Field(ge=0, le=1, default=0.7)
    reason: str = ""


class NewFinding(BaseModel):
    category: str
    title: str
    severity: Severity
    file_path: str
    line_start: int
    line_end: int | None = None
    snippet: str = ""
    reasoning: str = ""


class Final(BaseModel):
    verdicts: list[Verdict] = Field(default_factory=list)
    new_findings: list[NewFinding] = Field(default_factory=list)


class ReactStep(BaseModel):
    why: str = ""
    action: Action | None = None
    final: Final | None = None


def _summ(args: dict[str, Any]) -> str:
    return json.dumps(args, ensure_ascii=False)[:120]


async def _investigate_file(
    deps: Deps,
    state: dict[str, Any],
    model: Any,
    model_name: str,
    path: str,
    cands: list[dict[str, Any]],
    usage: list[dict[str, Any]],
) -> tuple[Final | None, list[str]]:
    cfg = limits().get("react", {})
    max_steps = int(cfg.get("max_steps", 6))
    token_cap = int(cfg.get("max_tokens_per_file", 12000))
    tid = state["target"]["target_id"]
    allowed = set(read_only_tools_for("code_investigate"))
    prompt = get_prompt("code_investigate")
    obs: list[str] = []
    used: list[str] = []
    spent = 0
    brief = [
        {
            "temp_id": c["temp_id"],
            "rule_id": c.get("rule_id"),
            "category": c["category"],
            "line": (c.get("evidence") or [{}])[0].get("line_start"),
            "snippet": (c.get("evidence") or [{}])[0].get("snippet_masked"),
        }
        for c in cands
    ]
    for step in range(max_steps):
        left = max_steps - step
        step_out = await structured_call(
            model,
            prompt,
            ReactStep,
            node="code_investigate",
            model_name=model_name,
            budget=deps.budget,
            usage=usage,
            path=path,
            candidates=wrap_untrusted(json.dumps(brief, ensure_ascii=False), "static_candidates")
            if brief
            else "(없음 — 위험 파일 휴리스틱으로 선택됨)",
            steps_left=left,
            observations="\n".join(obs) if obs else "(없음)",
        )
        if step_out.final is not None:
            return step_out.final, used
        act = step_out.action
        if act is None:
            return None, used
        if act.tool not in allowed:
            obs.append(f"[도구 거부] {act.tool} 는 이 노드에서 허용되지 않는다. 허용: {sorted(allowed)}")
            continue
        args = (
            {**act.args, "target_id": tid}
            if act.tool in ("read_repo_file", "search_repo", "list_repo_files")
            else dict(act.args)
        )
        await deps.event(
            state,
            "investigate.step",
            {"file": path, "tool": act.tool, "args_summary": _summ(act.args), "why": step_out.why[:200]},
            node="code_investigate",
        )
        try:
            res = await deps.call(state, "code_investigate", act.tool, **args)
            text = json.dumps(res, ensure_ascii=False)
        except AppError as e:
            text = f"error {e.code}: {e.message}"
        used.append(act.tool)
        spent += estimate_tokens(text)
        if spent > token_cap:  # 파일당 토큰 예산 (H8): 더 읽지 않고 판정하게 한다
            obs.append(wrap_untrusted(text[: token_cap * 2], f"{act.tool}:{path}"))
            obs.append("[토큰 예산 소진] 더 읽을 수 없다. 지금까지의 관찰로 final 을 낸다.")
            max_steps = step + 2
            continue
        obs.append(wrap_untrusted(text, f"{act.tool}:{path}"))
    # 스텝 소진: 마지막으로 final 만 요구
    obs.append("[스텝 소진] final 을 지금 낸다.")
    last = await structured_call(
        model,
        prompt,
        ReactStep,
        node="code_investigate",
        model_name=model_name,
        budget=deps.budget,
        usage=usage,
        path=path,
        candidates=wrap_untrusted(json.dumps(brief, ensure_ascii=False), "static_candidates") if brief else "(없음)",
        steps_left=0,
        observations="\n".join(obs),
    )
    return last.final, used


async def investigate(state: dict[str, Any], config: RunnableConfig, llm: tuple[Any, str]) -> dict[str, Any]:
    deps = get_deps(config)
    model, model_name = llm
    candidates: list[dict[str, Any]] = state.get("candidates", [])
    tid = state["target"]["target_id"]
    risky = state.get("plan", {}).get("repos", {}).get(tid, {}).get("risky_files", [])
    by_file: dict[str, list[dict[str, Any]]] = {}
    for c in candidates:
        p = ((c.get("evidence") or [{}])[0]).get("file_path") or ""
        by_file.setdefault(p, []).append(c)
    for p in risky:
        by_file.setdefault(p, [])
    usage: list[dict[str, Any]] = []
    out: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for path, cands in by_file.items():
        base = static_verdicts(cands)
        try:
            final, used = await _investigate_file(deps, state, model, model_name, path, base, usage)
        except AppError as e:
            if e.code == ErrorCode.BUDGET_EXCEEDED:  # 예산 소진: 남은 파일은 정적 판정으로
                await deps.event(
                    state, "budget.exceeded", {"node": "code_investigate", "file": path}, node="code_investigate"
                )
                out.extend(base)
                continue
            errors.append({"node": "code_investigate", "file": path, "code": str(e.code), "message": e.message})
            out.extend(base)
            continue
        if final is None:
            out.extend(base)
            continue
        verdicts = {v.temp_id: v for v in final.verdicts}
        for c in base:
            v = verdicts.get(c["temp_id"])
            c["tools_used"] = sorted({*c.get("tools_used", []), *used})
            if v is None:
                out.append(c)
                continue
            if c["category"] == "AUDITOR_INJECTION_ATTEMPT":  # 감사기 인젝션 보고는 모델이 기각할 수 없다
                out.append(c)
                continue
            c.update(
                verdict=v.verdict,
                confidence=round(max(0.05, min(0.99, v.confidence)), 2),
                reasoning_summary=(v.reason or c["reasoning_summary"])[:400],
            )
            out.append(c)
        for nf in final.new_findings:
            if nf.file_path != path and path:
                continue
            out.append(
                FindingDraft(
                    temp_id=f"r-{uuid.uuid4().hex[:8]}",
                    target_id=tid,
                    source="code",
                    category=nf.category,
                    rule_id="LLM-REACT",
                    title=nf.title[:200],
                    severity=nf.severity,
                    confidence=0.75,
                    evidence=[
                        Evidence(
                            kind="code",
                            file_path=nf.file_path,
                            line_start=nf.line_start,
                            line_end=nf.line_end or nf.line_start,
                            snippet_masked=nf.snippet[:300],
                        )
                    ],
                    reasoning_summary=nf.reasoning[:400],
                    tools_used=sorted({*used, "read_repo_file"}),
                    verdict="TRUE_POSITIVE",
                ).model_dump()
            )
    return {"investigated": out, "errors": errors, "token_usage": usage}


__all__ = ["investigate", "LLMFactory"]
