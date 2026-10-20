"""agent_static_check: 매니페스트에 7.3 규칙 실행. AGT-PROMPT-002 는 LLM 이 있으면 재판정."""

from __future__ import annotations

import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel

from core.errors import AppError
from core.graph.deps import get_deps, node_error
from core.graph.nodes._base import node
from core.schemas import Evidence, FindingDraft
from harness.context import UNTRUSTED_RULE, wrap_untrusted
from harness.llm import structured_call
from harness.prompts import get_prompt


class HierarchyJudgement(BaseModel):
    has_instruction_hierarchy: bool
    treats_external_as_data: bool
    reasoning: str


def hit_to_agent_draft(target_id: str, h: dict[str, Any]) -> FindingDraft:
    subject = h["extra"].get("tool") or h["extra"].get("env") or h["extra"].get("target") or "manifest"
    return FindingDraft(
        temp_id=f"a-{uuid.uuid4().hex[:8]}",
        target_id=target_id,
        source="agent",
        category=h["category"],
        rule_id=h["rule_id"],
        title=f"{h['category']}: {subject}",
        severity=h["severity"],
        confidence=0.85,
        evidence=[
            Evidence(
                kind="manifest",
                file_path=h["path"],
                line_start=h["line"],
                line_end=h["line"],
                snippet_masked=h["snippet_masked"],
            )
        ],
        reasoning_summary=str(h["extra"].get("reason") or f"Agent 규칙 {h['rule_id']} 위반."),
        tools_used=["run_agent_rule_check"],
        verdict="TRUE_POSITIVE",
        standard_refs=list(h.get("references", [])),
        fix_hint=h.get("fix_hint"),
    )


@node("agent_static_check")
async def agent_static_check(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    tid = state["target"]["target_id"]
    res = await deps.call(state, "agent_static_check", "run_agent_rule_check", target_id=tid)
    manifest = await deps.call(state, "agent_static_check", "get_agent_manifest", target_id=tid)
    drafts = [hit_to_agent_draft(tid, h) for h in res["hits"]]
    usage: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    llm = deps.llm("agent_static_check", state.get("eval_config"))
    if llm is not None:
        model, name = llm
        try:
            j = await structured_call(
                model,
                get_prompt("agent_prompt_hierarchy"),
                HierarchyJudgement,
                node="agent_static_check",
                model_name=name,
                budget=deps.budget,
                usage=usage,
                untrusted_rule=UNTRUSTED_RULE,
                system_prompt=wrap_untrusted(manifest["system_prompt_masked"], "agent:system_prompt"),
            )
            has_rule = j.has_instruction_hierarchy and j.treats_external_as_data
            static_hit = [d for d in drafts if d.category == "PROMPT_NO_INSTRUCTION_HIERARCHY"]
            if has_rule and static_hit:
                drafts = [d for d in drafts if d.category != "PROMPT_NO_INSTRUCTION_HIERARCHY"]
            for d in static_hit:
                d.reasoning_summary = j.reasoning
                d.tools_used.append("llm_judge")
        except AppError as e:
            errors.append(node_error("agent_static_check", e))
    await deps.event(
        state,
        "agent.rules",
        {"hits": len(drafts), "code_secret_names": res["code_secret_names"]},
        node="agent_static_check",
    )
    return {
        "agent_findings": [d.model_dump() for d in drafts] + res.get("source_findings", []),
        "manifest": manifest["manifest"] | {"_source": manifest.get("source"), "_runtime_protocol": manifest.get("runtime_protocol", "auditor")},
        "token_usage": usage,
        "errors": errors,
    }
