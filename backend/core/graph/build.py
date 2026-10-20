"""StateGraph 조립 — 명세 8.1 다이어그램과 1:1.

START → intake ─Send→ code_audit ┐
                └Send→ agent_audit ┴→ correlate ─(CRITICAL?)→ human_approval ─승인→ fix_generate
                                              │                     └반려→ risk_accept → fix_generate
                                              └(없음)→ fix_generate
fix_generate ─(수정안 있음)→ apply_fix → wait_merge → re_verify ─해결→ report
             └(0건)→ report                                  ├미해결 & retry<3 → fix_generate
                                                             └미해결 & retry≥3 → mark_manual → report → END
code_audit:  code_static_scan → code_investigate → code_evidence_check
agent_audit: agent_static_check → probe_generate → probe_run → probe_judge ─(불확실 & 라운드<2)→ probe_generate
"""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from core.graph.nodes.agent_static_check import agent_static_check
from core.graph.nodes.apply_fix import apply_fix
from core.graph.nodes.code_evidence_check import code_evidence_check
from core.graph.nodes.code_investigate import code_investigate
from core.graph.nodes.code_static_scan import code_static_scan
from core.graph.nodes.correlate import correlate
from core.graph.nodes.fix_generate import fix_generate
from core.graph.nodes.human_approval import human_approval
from core.graph.nodes.intake import intake
from core.graph.nodes.mark_manual import mark_manual
from core.graph.nodes.probe_generate import probe_generate
from core.graph.nodes.probe_judge import probe_judge
from core.graph.nodes.probe_run import probe_run
from core.graph.nodes.re_verify import re_verify
from core.graph.nodes.report import report
from core.graph.nodes.risk_accept import risk_accept
from core.graph.nodes.wait_merge import wait_merge
from core.graph.state import AgentAuditOutput, AgentAuditState, AuditState, CodeAuditOutput, CodeAuditState
from harness.budget import limits

MAX_FIX_RETRIES = 3


def build_code_audit() -> CompiledStateGraph:  # type: ignore[type-arg]
    g = StateGraph(CodeAuditState, output_schema=CodeAuditOutput)
    g.add_node("code_static_scan", code_static_scan)  # type: ignore[call-overload,arg-type]
    g.add_node("code_investigate", code_investigate)  # type: ignore[call-overload,arg-type]
    g.add_node("code_evidence_check", code_evidence_check)  # type: ignore[call-overload,arg-type]
    g.add_edge(START, "code_static_scan")
    g.add_edge("code_static_scan", "code_investigate")
    g.add_edge("code_investigate", "code_evidence_check")
    g.add_edge("code_evidence_check", END)
    return g.compile()


def route_probe_loop(state: dict[str, Any]) -> str:
    max_rounds = int(limits().get("probe", {}).get("max_rounds", 2))
    if state.get("uncertain_probe_ids") and int(state.get("probe_round", 0)) < max_rounds:
        return "probe_generate"
    return END


def build_agent_audit() -> CompiledStateGraph:  # type: ignore[type-arg]
    g = StateGraph(AgentAuditState, output_schema=AgentAuditOutput)
    g.add_node("agent_static_check", agent_static_check)  # type: ignore[call-overload,arg-type]
    g.add_node("probe_generate", probe_generate)  # type: ignore[call-overload,arg-type]
    g.add_node("probe_run", probe_run)  # type: ignore[call-overload,arg-type]
    g.add_node("probe_judge", probe_judge)  # type: ignore[call-overload,arg-type]
    g.add_edge(START, "agent_static_check")
    g.add_edge("agent_static_check", "probe_generate")
    g.add_edge("probe_generate", "probe_run")
    g.add_edge("probe_run", "probe_judge")
    g.add_conditional_edges("probe_judge", route_probe_loop, ["probe_generate", END])
    return g.compile()


def _sub_input(state: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    return {
        "audit_id": state["audit_id"],
        "eval_config": state.get("eval_config"),
        "actor_role": state.get("actor_role", "ADMIN"),
        "requested_by": state.get("requested_by"),
        "target": target,
        "plan": state.get("plan", {}),
        "probe_round": 0,
    }


def route_audits(state: dict[str, Any]) -> list[Send] | str:
    mode = state.get("mode", "full")
    sends = []
    for t in state.get("targets", []):
        if t["type"] == "repo" and mode in ("full", "code"):
            sends.append(Send("code_audit", _sub_input(state, t)))
        if t["type"] == "agent" and mode in ("full", "agent"):
            sends.append(Send("agent_audit", _sub_input(state, t)))
    return sends or "correlate"


def route_after_correlate(state: dict[str, Any]) -> str:
    if state.get("eval_config"):  # 비교 실험: 승인·수정 없이 report 까지만
        return "report"
    return "human_approval" if state.get("needs_approval") else "fix_generate"


def route_approval(state: dict[str, Any]) -> str:
    return "fix_generate" if (state.get("approval") or {}).get("decision") == "APPROVE" else "risk_accept"


def route_fix(state: dict[str, Any]) -> str:
    if state.get("fix_proposals"):
        return "apply_fix"
    return "mark_manual" if state.get("retry_count") else "report"


def route_merge(state: dict[str, Any]) -> str:
    return "re_verify" if state.get("merge_status") == "MERGED" else "report"


def route_verify(state: dict[str, Any]) -> str:
    v = state.get("verify_result") or {}
    if not v.get("unresolved"):
        return "report"
    return "fix_generate" if int(state.get("retry_count", 0)) < MAX_FIX_RETRIES else "mark_manual"


def build_graph(checkpointer: BaseCheckpointSaver | None = None) -> CompiledStateGraph:  # type: ignore[type-arg]
    g = StateGraph(AuditState)
    g.add_node("intake", intake)  # type: ignore[call-overload,arg-type]
    g.add_node("code_audit", build_code_audit())
    g.add_node("agent_audit", build_agent_audit())
    g.add_node("correlate", correlate)  # type: ignore[call-overload,arg-type]
    g.add_node("human_approval", human_approval)  # type: ignore[call-overload,arg-type]
    g.add_node("risk_accept", risk_accept)  # type: ignore[call-overload,arg-type]
    g.add_node("fix_generate", fix_generate)  # type: ignore[call-overload,arg-type]
    g.add_node("apply_fix", apply_fix)  # type: ignore[call-overload,arg-type]
    g.add_node("wait_merge", wait_merge)  # type: ignore[call-overload,arg-type]
    g.add_node("re_verify", re_verify)  # type: ignore[call-overload,arg-type]
    g.add_node("mark_manual", mark_manual)  # type: ignore[call-overload,arg-type]
    g.add_node("report", report)  # type: ignore[call-overload,arg-type]

    g.add_edge(START, "intake")
    g.add_conditional_edges("intake", route_audits, ["code_audit", "agent_audit", "correlate"])
    g.add_edge("code_audit", "correlate")
    g.add_edge("agent_audit", "correlate")
    g.add_conditional_edges("correlate", route_after_correlate, ["human_approval", "fix_generate", "report"])
    g.add_conditional_edges("human_approval", route_approval, ["fix_generate", "risk_accept"])
    g.add_edge("risk_accept", "fix_generate")
    g.add_conditional_edges("fix_generate", route_fix, ["apply_fix", "mark_manual", "report"])
    g.add_edge("apply_fix", "wait_merge")
    g.add_conditional_edges("wait_merge", route_merge, ["re_verify", "report"])
    g.add_conditional_edges("re_verify", route_verify, ["report", "fix_generate", "mark_manual"])
    g.add_edge("mark_manual", "report")
    g.add_edge("report", END)
    return g.compile(checkpointer=checkpointer)
