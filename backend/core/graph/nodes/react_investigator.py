"""ReAct 조사 (명세 8.5 (1); DECISIONS #17).

정적 후보가 있는 파일 + 위험 파일 휴리스틱 상위 K 개를 파일마다 최대 `react.max_steps`(6) 스텝으로 조사한다.
파일 원문은 처음부터 주지 않고 에이전트가 read_repo_file 로 필요한 범위를 읽는다(도구 선택 증거).
모든 도구 호출은 H3 게이트를 거치며 investigate.step 이벤트로 {tool, args_summary, why} 가 나간다.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from collections import Counter
from functools import lru_cache
from typing import Any, Literal

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field, field_validator

from core.errors import AppError, ErrorCode
from core.graph.deps import Deps, LLMFactory, get_deps
from core.graph.nodes.code_investigate_static import static_verdicts
from core.schemas import Evidence, FindingDraft, Severity
from harness.budget import limits
from harness.context import UNTRUSTED_RULE, compact_observations, estimate_tokens, truncate_tool_result, wrap_untrusted
from harness.llm import structured_call
from harness.prompts import get_prompt
from harness.tools import read_only_tools_for

TEST_PATH = re.compile(r"(^|/)src/test/")


class Action(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class Verdict(BaseModel):
    temp_id: str
    verdict: Literal["TRUE_POSITIVE", "FALSE_POSITIVE", "NEEDS_REVIEW"]
    confidence: float = Field(ge=0, le=1, default=0.7)
    reason: str = ""


@lru_cache
def allowed_categories() -> frozenset[str]:
    from core.rules.models import load_rules

    return frozenset({r.category for r in load_rules("code")})


@lru_cache
def category_defs() -> str:
    from core.rules.models import load_rules

    names: dict[str, list[str]] = {}
    for r in load_rules("code"):
        names.setdefault(r.category, []).append(r.name)
    return "\n    ".join(f"- {c}: {' / '.join(n)}" for c, n in sorted(names.items()))


class NewFinding(BaseModel):
    category: str

    @field_validator("category")
    @classmethod
    def _known(cls, v: str) -> str:
        # 자유 문자열 카테고리는 매칭·집계가 안 되므로 거부 → H4 스키마 재요청
        if v not in allowed_categories():
            raise ValueError(f"category must be one of {sorted(allowed_categories())}")
        return v

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


class ContextSummary(BaseModel):
    summary: str


async def _investigate_file(
    deps: Deps,
    state: dict[str, Any],
    model: Any,
    model_name: str,
    path: str,
    cands: list[dict[str, Any]],
    usage: list[dict[str, Any]],
) -> tuple[Final | None, list[str]]:
    """파일 1개 ReAct. Harness Agent Loop 한도: LLM 턴·작업 시간·같은 도구 횟수 (config/budget.yaml react)."""
    cfg = limits().get("react", {})
    max_steps = int(cfg.get("max_steps", 6))
    max_turns = int(cfg.get("max_llm_turns", 8))
    deadline = time.monotonic() + float(cfg.get("max_seconds", 120))
    max_same = int(cfg.get("max_same_tool_calls", 3))
    max_chars = int(cfg.get("tool_result_max_chars", 8000))
    ctx_budget = int(cfg.get("context_tokens", 6000))
    token_cap = int(cfg.get("max_tokens_per_file", 12000))
    tid = state["target"]["target_id"]
    allowed = set(read_only_tools_for("code_investigate"))
    prompt = get_prompt("code_investigate")
    obs: list[str] = []
    used: list[str] = []
    tool_counts: Counter[str] = Counter()
    spent = 0
    turns = 0
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
    cand_text = (
        wrap_untrusted(json.dumps(brief, ensure_ascii=False), "static_candidates")
        if brief
        else "(없음 — 위험 파일 휴리스틱으로 선택됨)"
    )

    async def summarize(old: list[str]) -> str:
        nonlocal turns
        turns += 1
        out = await structured_call(
            model,
            get_prompt("context_summarize"),
            ContextSummary,
            node="code_investigate",
            model_name=model_name,
            budget=deps.budget,
            usage=usage,
            untrusted_rule=UNTRUSTED_RULE,
            observations="\n".join(old),
        )
        return out.summary

    async def ask(steps_left: int) -> ReactStep:
        nonlocal turns
        turns += 1
        return await structured_call(
            model,
            prompt,
            ReactStep,
            node="code_investigate",
            model_name=model_name,
            budget=deps.budget,
            usage=usage,
            path=path,
            candidates=cand_text,
            steps_left=steps_left,
            observations="\n".join(obs) if obs else "(없음)",
            categories=", ".join(sorted(allowed_categories())),
            category_defs=category_defs(),
            untrusted_rule=UNTRUSTED_RULE,
        )

    stop_reason = "steps"
    forced_read = False
    for step in range(max_steps):
        if turns >= max_turns - 1:  # 마지막 1턴은 final 용으로 남긴다
            stop_reason = "llm_turns"
            break
        if time.monotonic() >= deadline:
            stop_reason = "timeout"
            break
        obs, compacted = await compact_observations(obs, ctx_budget, summarize)
        if compacted:
            await deps.event(
                state, "context.compacted", {"file": path, "budget_tokens": ctx_budget}, node="code_investigate"
            )
        step_out = await ask(max_steps - step)
        if step_out.final is not None:
            if not brief and "read_repo_file" not in used and not forced_read:
                # Harness: 정적 후보 없는 위험 파일을 읽지 않고 결론 내는 것을 막는다 (1회)
                forced_read = True
                obs.append("[규칙] 이 파일은 아직 읽지 않았다. read_repo_file 로 파일을 읽은 뒤 final 을 낸다.")
                continue
            return step_out.final, used
        act = step_out.action
        if act is None:
            return None, used
        if act.tool not in allowed:
            obs.append(f"[도구 거부] {act.tool} 는 이 노드에서 허용되지 않는다. 허용: {sorted(allowed)}")
            continue
        if tool_counts[act.tool] >= max_same:
            obs.append(
                f"[반복 제한] {act.tool} 는 이 파일에서 이미 {max_same}회 호출했다. 다른 도구를 쓰거나 final 을 낸다."
            )
            continue
        tool_counts[act.tool] += 1
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
            text = truncate_tool_result(json.dumps(res, ensure_ascii=False), max_chars)
        except AppError as e:  # 오류도 관찰로 돌려줘 모델이 다음 행동을 고르게 한다
            text = f"error {e.code}: {e.message}"
        used.append(act.tool)
        spent += estimate_tokens(text)
        obs.append(wrap_untrusted(text, f"{act.tool}:{path}"))
        if spent > token_cap:  # 파일당 토큰 예산 (H8): 더 읽지 않고 판정하게 한다
            stop_reason = "token_budget"
            break
    if stop_reason != "steps":
        await deps.event(
            state, "loop.limit", {"file": path, "reason": stop_reason, "turns": turns}, node="code_investigate"
        )
    obs.append(f"[중단: {stop_reason}] 더 조회할 수 없다. final 을 지금 낸다.")
    last = await ask(0)
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
            if v.verdict == "FALSE_POSITIVE" and float(c.get("confidence", 0)) >= 0.8 and not TEST_PATH.search(path):
                # 확정형 정적 규칙(needs_llm_review=false)은 LLM 이 지울 수 없다: '테스트용 값' 주석 같은 회피에 대비해 검토 필요로만
                c.update(
                    verdict="NEEDS_REVIEW",
                    confidence=0.6,
                    reasoning_summary=f"[정적 확정 규칙 — LLM 기각 보류] {v.reason or ''}"[:400],
                )
                out.append(c)
                continue
            c.update(
                verdict=v.verdict,
                confidence=round(max(0.05, min(0.99, v.confidence)), 2),
                reasoning_summary=(v.reason or c["reasoning_summary"])[:400],
            )
            out.append(c)
        known_lines = [int((c.get("evidence") or [{}])[0].get("line_start") or 0) for c in base]
        for nf in final.new_findings:
            if nf.file_path != path and path:
                continue
            if any(abs(nf.line_start - ln) <= 3 for ln in known_lines):  # 정적 후보와 같은 위치의 재보고는 중복
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
