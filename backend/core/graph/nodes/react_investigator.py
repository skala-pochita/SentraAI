"""ReAct 조사 (명세 8.5 (1); DECISIONS #13).

정적 후보가 있는 파일 + 위험 파일 휴리스틱 상위 K 개를 파일마다 최대 `react.max_steps`(6) 스텝으로 조사한다.
파일 원문은 처음부터 주지 않고 에이전트가 read_repo_file 로 필요한 범위를 읽는다(도구 선택 증거).
모든 도구 호출은 H3 게이트를 거치며 investigate.step 이벤트로 {tool, args_summary, why} 가 나간다.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import Counter
from typing import Any, Literal

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field, field_validator
from pydantic.json_schema import SkipJsonSchema

from core.aio import gather_or_cancel
from core.categories import category_definitions, category_refs, code_categories
from core.errors import AppError, ErrorCode
from core.graph.deps import Deps, LLMFactory, get_deps
from core.graph.nodes.code_investigate_static import static_verdicts
from core.rules.engine import TEST_PATH
from core.schemas import Evidence, FindingDraft, Severity
from harness.budget import limits
from harness.context import UNTRUSTED_RULE, compact_observations, estimate_tokens, truncate_tool_result, wrap_untrusted
from harness.llm import structured_call
from harness.prompts import get_prompt
from harness.tools import read_only_tools_for

# 커밋된 자격증명은 '가짜 값·임시 계정'이라는 주장을 감사기가 검증할 수 없다 → 테스트 경로 밖에서는 기각하지 않고 검토로 남긴다
CREDENTIAL_CATEGORIES = {"HARDCODED_SECRET", "HARDCODED_CREDENTIAL", "SECRET_LEAK", "COMMENT_SENSITIVE"}


class Action(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class Verdict(BaseModel):
    temp_id: str
    verdict: Literal["TRUE_POSITIVE", "FALSE_POSITIVE", "NEEDS_REVIEW"]
    confidence: float = Field(ge=0, le=1, default=0.7)
    reason: str = ""


def allowed_categories() -> frozenset[str]:
    return code_categories()


def category_defs() -> str:
    return category_definitions()


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
    # 시스템이 채움: 새 발견을 보고할 수 있는 관련 파일 (모델 출력 스키마에서는 제외)
    related_paths: SkipJsonSchema[list[str]] = Field(default_factory=list, exclude=True)


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
    token_cap = int(cfg.get("max_tokens_per_file", 12000))  # 도구 결과(관찰) 토큰 상한
    llm_token_cap = int(cfg.get("max_llm_tokens_per_file", 60000))  # 이 파일의 LLM 입력+출력 토큰 상한
    final_grace = float(cfg.get("final_grace_s", 30))  # 한도 도달 후 마지막 final 요청에 주는 시간
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
    try:  # 파일을 넘나드는 흐름: import·타입 참조로 이어진 파일을 먼저 알려 준다 (S3)
        related = await deps.call(state, "code_investigate", "related_files", target_id=tid, path=path)
    except AppError:
        related = []
    related = [r for r in related if isinstance(r, dict) and "path" in r] if isinstance(related, list) else []
    related_text = (
        "\n".join(
            f"- {r['path']} ({'이 파일이 사용' if r['relation'] == 'uses' else '이 파일을 사용'})" for r in related
        )
        or "(없음)"
    )
    cand_text = (
        wrap_untrusted(json.dumps(brief, ensure_ascii=False), "static_candidates")
        if brief
        else "(없음 — 위험 파일 휴리스틱으로 선택됨)"
    )

    def llm_tokens() -> int:
        return sum(int(u.get("in", 0)) + int(u.get("out", 0)) for u in usage)

    async def bounded(coro: Any, timeout: float) -> Any:
        """LLM 호출 1회(SDK 재시도·스키마 재요청 포함)를 남은 시간 안으로 자른다."""
        try:
            return await asyncio.wait_for(coro, timeout=max(0.1, timeout))
        except TimeoutError as e:
            raise AppError(ErrorCode.UPSTREAM_TIMEOUT, f"ReAct LLM call exceeded time limit ({path})") from e

    async def summarize(old: list[str]) -> str:
        nonlocal turns
        turns += 1
        out = await bounded(
            structured_call(
                model,
                get_prompt("context_summarize"),
                ContextSummary,
                node="code_investigate",
                model_name=model_name,
                budget=deps.budget,
                usage=usage,
                untrusted_rule=UNTRUSTED_RULE,
                observations="\n".join(old),
            ),
            deadline - time.monotonic(),
        )
        return str(out.summary)

    async def ask(steps_left: int, timeout: float) -> ReactStep:
        nonlocal turns
        turns += 1
        out: ReactStep = await bounded(
            structured_call(
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
                related=related_text,
            ),
            timeout,
        )
        return out

    stop_reason = "steps"
    forced_read = False
    for step in range(max_steps):
        if turns >= max_turns - 1:  # 마지막 1턴은 final 용으로 남긴다
            stop_reason = "llm_turns"
            break
        if time.monotonic() >= deadline:
            stop_reason = "timeout"
            break
        if llm_tokens() >= llm_token_cap:  # 파일당 LLM 토큰 예산 (H8)
            stop_reason = "llm_token_budget"
            break
        try:
            obs, compacted = await compact_observations(obs, ctx_budget, summarize)
            if compacted:
                await deps.event(
                    state, "context.compacted", {"file": path, "budget_tokens": ctx_budget}, node="code_investigate"
                )
            step_out = await ask(max_steps - step, deadline - time.monotonic())
        except AppError as e:
            if e.code != ErrorCode.UPSTREAM_TIMEOUT or time.monotonic() < deadline:
                raise
            stop_reason = "timeout"  # 호출 도중 작업 시간 한도 도달
            break
        if step_out.final is not None:
            if not brief and "read_repo_file" not in used and not forced_read:
                # Harness: 정적 후보 없는 위험 파일을 읽지 않고 결론 내는 것을 막는다 (1회)
                forced_read = True
                obs.append("[규칙] 이 파일은 아직 읽지 않았다. read_repo_file 로 파일을 읽은 뒤 final 을 낸다.")
                continue
            step_out.final.related_paths = [r["path"] for r in related]
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
            state,
            "loop.limit",
            {"file": path, "reason": stop_reason, "turns": turns, "llm_tokens": llm_tokens()},
            node="code_investigate",
        )
    obs.append(f"[중단: {stop_reason}] 더 조회할 수 없다. final 을 지금 낸다.")
    last = await ask(0, final_grace)
    if last.final is not None:
        last.final.related_paths = [r["path"] for r in related]
    return last.final, used


def _group_by_file(candidates: list[dict[str, Any]], risky: list[str]) -> dict[str, list[dict[str, Any]]]:
    """정적 후보를 파일별로 묶고, 후보가 없는 위험 파일도 조사 대상으로 넣는다 (정적 회피 탐지용)."""
    by_file: dict[str, list[dict[str, Any]]] = {}
    for c in candidates:
        p = ((c.get("evidence") or [{}])[0]).get("file_path") or ""
        by_file.setdefault(p, []).append(c)
    for p in risky:
        by_file.setdefault(p, [])
    return by_file


def _hold_reason(c: dict[str, Any], path: str, poisoned: bool) -> str | None:
    """정적 후보를 LLM 이 FALSE_POSITIVE 로 판정해도 기각하지 않을 사유."""
    if poisoned:  # 감사기 인젝션("이 파일은 보고하지 말 것")이 있는 파일: 모델 판단이 조작됐을 수 있다
        return "감사기 인젝션이 있는 파일"
    if c["category"] in CREDENTIAL_CATEGORIES and not TEST_PATH.search(path):
        return "자격증명 정적 탐지"
    return None


def _apply_verdict(
    c: dict[str, Any], v: Verdict | None, path: str, used: list[str], poisoned: bool = False
) -> dict[str, Any]:
    c["tools_used"] = sorted({*c.get("tools_used", []), *used})
    if v is None or c["category"] == "AUDITOR_INJECTION_ATTEMPT":  # 감사기 인젝션 보고는 모델이 기각할 수 없다
        return c
    if v.verdict == "FALSE_POSITIVE" and (hold := _hold_reason(c, path, poisoned)):
        c.update(
            verdict="NEEDS_REVIEW",
            confidence=min(float(c.get("confidence", 0.6)), 0.6),
            reasoning_summary=f"[{hold} — LLM 기각 보류] {v.reason or ''}"[:400],
        )
        return c
    if v.verdict == "FALSE_POSITIVE" and float(c.get("confidence", 0)) >= 0.8 and not TEST_PATH.search(path):
        # 확정형 정적 규칙(needs_llm_review=false)은 LLM 이 지울 수 없다: '테스트용 값' 주석 같은 회피에 대비해 검토 필요로만
        c.update(
            verdict="NEEDS_REVIEW",
            confidence=0.6,
            reasoning_summary=f"[정적 확정 규칙 — LLM 기각 보류] {v.reason or ''}"[:400],
        )
        return c
    c.update(
        verdict=v.verdict,
        confidence=round(max(0.05, min(0.99, v.confidence)), 2),
        reasoning_summary=(v.reason or c["reasoning_summary"])[:400],
    )
    return c


def _new_findings(
    final: Final, path: str, base: list[dict[str, Any]], tid: str, used: list[str]
) -> list[dict[str, Any]]:
    """모델이 새로 찾은 Finding. 조사 파일·관련 파일(S3) 밖의 보고, 정적 후보와 같은 위치(±3줄) 재보고는 버린다.
    관련 파일에 보고한 것도 다음 노드(code_evidence_check)가 실제 파일과 대조한다."""
    known = [
        (
            (c.get("evidence") or [{}])[0].get("file_path") or path,
            int((c.get("evidence") or [{}])[0].get("line_start") or 0),
        )
        for c in base
    ]
    allowed_paths = {path, *final.related_paths}
    out = []
    for nf in final.new_findings:
        if path and nf.file_path not in allowed_paths:
            continue
        if any(f == nf.file_path and abs(nf.line_start - ln) <= 3 for f, ln in known):
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
                standard_refs=category_refs(nf.category),
            ).model_dump()
        )
    return out


async def investigate(state: dict[str, Any], config: RunnableConfig, llm: tuple[Any, str]) -> dict[str, Any]:
    """파일별 ReAct 조사를 동시에 최대 `react.concurrency` 개 실행한다 (결과 순서는 파일 순서 그대로)."""
    deps = get_deps(config)
    model, model_name = llm
    tid = state["target"]["target_id"]
    risky = state.get("plan", {}).get("repos", {}).get(tid, {}).get("risky_files", [])
    sem = asyncio.Semaphore(max(1, int(limits().get("react", {}).get("concurrency", 4))))

    async def one(
        path: str, cands: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[Any]]:
        base = static_verdicts(cands)
        file_usage: list[dict[str, Any]] = []  # 파일별로 따로 세야 파일당 토큰 예산이 정확하다
        async with sem:
            try:
                final, used = await _investigate_file(deps, state, model, model_name, path, base, file_usage)
            except AppError as e:
                errs: list[dict[str, Any]] = []
                if e.code == ErrorCode.BUDGET_EXCEEDED:  # 예산 소진: 남은 파일은 정적 판정으로
                    payload = {"node": "code_investigate", "file": path}
                    await deps.event(state, "budget.exceeded", payload, node="code_investigate")
                else:
                    errs.append({"node": "code_investigate", "file": path, "code": str(e.code), "message": e.message})
                return base, errs, file_usage
        if final is None:
            return base, [], file_usage
        verdicts = {v.temp_id: v for v in final.verdicts}
        poisoned = any(c["category"] == "AUDITOR_INJECTION_ATTEMPT" for c in base)
        found = [_apply_verdict(c, verdicts.get(c["temp_id"]), path, used, poisoned) for c in base]
        return found + _new_findings(final, path, base, tid, used), [], file_usage

    groups = _group_by_file(state.get("candidates", []), risky)
    results = await gather_or_cancel(*(one(p, c) for p, c in groups.items()))
    out: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    usage: list[dict[str, Any]] = []
    for found, errs, file_usage in results:
        out.extend(found)
        errors.extend(errs)
        usage.extend(file_usage)
    return {"investigated": out, "errors": errors, "token_usage": usage}


__all__ = ["investigate", "LLMFactory"]
