"""LLM 수정안: 결정론 수정기가 없는 Finding 에 대해 edits(search/replace)를 생성한다.

파일 내용은 마스킹·라인 번호 + <untrusted> 로 감싸서 제공하고, 마스킹된 값(****)은 edits 에 넣지 못하게 한다.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from core.errors import AppError
from core.graph.deps import get_deps
from core.schemas import FindingDraft
from harness.context import UNTRUSTED_RULE, number_lines, wrap_untrusted
from harness.llm import structured_call
from harness.prompts import get_prompt


class Edit(BaseModel):
    op: Literal["replace", "line_regex", "delete_line", "append"]
    path: str
    old: str | None = None
    new: str | None = None
    line: int | None = None
    pattern: str | None = None
    replacement: str | None = None
    text: str | None = None


class FixPlan(BaseModel):
    edits: list[Edit] = Field(default_factory=list)
    explanation: str = ""


def _safe(e: Edit) -> bool:
    blob = " ".join(x or "" for x in (e.old, e.new, e.pattern, e.replacement, e.text))
    return "****" not in blob


async def llm_edits(
    state: dict[str, Any],
    config: RunnableConfig,
    llm: tuple[BaseChatModel, str],
    findings: list[FindingDraft],
    feedback: list[str],
    usage: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    deps = get_deps(config)
    model, name = llm
    types = {t["target_id"]: t for t in state.get("targets", [])}
    out: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {"edits": [], "finding_ids": []})
    for f in findings:
        t = types.get(f.target_id, {})
        ev = f.primary
        if t.get("type") != "repo" or not ev or not ev.file_path:
            continue
        try:
            r = await deps.call(state, "fix_generate", "read_repo_file", target_id=f.target_id, path=ev.file_path)
            guide = await deps.call(
                state, "fix_generate", "search_framework_guide", query=f"{f.category} {f.title}", k=2
            )
            plan = await structured_call(
                model,
                get_prompt("fix_generate"),
                FixPlan,
                node="fix_generate",
                model_name=name,
                budget=deps.budget,
                usage=usage,
                untrusted_rule=UNTRUSTED_RULE,
                finding=f.model_dump_json(include={"category", "title", "severity", "evidence", "fix_hint"}),
                file=wrap_untrusted(number_lines(r["content_masked"]), f"repo:{ev.file_path}"),
                guide="\n".join(g["text"][:600] for g in guide),
                feedback="\n".join(feedback[-5:]) or "(없음)",
            )
        except AppError:
            continue
        edits = [e.model_dump(exclude_none=True) for e in plan.edits if _safe(e) and e.path == ev.file_path]
        if edits:
            out[(f.target_id, "code_patch")]["edits"].extend(edits)
            out[(f.target_id, "code_patch")]["finding_ids"].append(f.finding_id)
    return dict(out)
