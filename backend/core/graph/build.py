"""Read-only audit: intake → code/agent analysis → correlate → report."""

from __future__ import annotations

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from core.graph.nodes.agent_static_check import agent_static_check
from core.graph.nodes.code_evidence_check import code_evidence_check
from core.graph.nodes.code_investigate import code_investigate
from core.graph.nodes.code_static_scan import code_static_scan
from core.graph.nodes.correlate import correlate
from core.graph.nodes.intake import intake
from core.graph.nodes.prepare_sandbox import prepare_sandbox
from core.graph.nodes.probe_generate import probe_generate
from core.graph.nodes.probe_judge import probe_judge
from core.graph.nodes.probe_run import probe_run
from core.graph.nodes.report import report
from core.graph.state import AgentAuditOutput, AgentAuditState, AuditState, CodeAuditOutput, CodeAuditState
from harness.budget import limits


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
    g.add_node("prepare_sandbox", prepare_sandbox)  # type: ignore[call-overload,arg-type]
    g.add_node("probe_generate", probe_generate)  # type: ignore[call-overload,arg-type]
    g.add_node("probe_run", probe_run)  # type: ignore[call-overload,arg-type]
    g.add_node("probe_judge", probe_judge)  # type: ignore[call-overload,arg-type]
    g.add_edge(START, "agent_static_check")
    g.add_edge("agent_static_check", "prepare_sandbox")
    g.add_edge("prepare_sandbox", "probe_generate")
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
    return "report"


def build_graph(checkpointer: BaseCheckpointSaver | None = None) -> CompiledStateGraph:  # type: ignore[type-arg]
    g = StateGraph(AuditState)
    g.add_node("intake", intake)  # type: ignore[call-overload,arg-type]
    g.add_node("code_audit", build_code_audit())
    g.add_node("agent_audit", build_agent_audit())
    g.add_node("correlate", correlate)  # type: ignore[call-overload,arg-type]
    g.add_node("report", report)  # type: ignore[call-overload,arg-type]

    g.add_edge(START, "intake")
    g.add_conditional_edges("intake", route_audits, ["code_audit", "agent_audit", "correlate"])
    g.add_edge("code_audit", "correlate")
    g.add_edge("agent_audit", "correlate")
    g.add_edge("correlate", "report")
    g.add_edge("report", END)
    return g.compile(checkpointer=checkpointer)
