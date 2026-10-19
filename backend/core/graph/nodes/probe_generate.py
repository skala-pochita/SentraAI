"""probe_generate: 기본 probe 로드 + (LLM 있으면) 매니페스트 도구 이름·설명을 반영한 적응형 변형 생성 (라운드당 ≤4)."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel, Field

from core.errors import AppError
from core.graph.deps import get_deps, node_error
from core.graph.nodes._base import node
from core.probes import load_probe_set
from harness.budget import limits
from harness.llm import structured_call
from harness.prompts import get_prompt


class AdaptiveProbe(BaseModel):
    base_probe_id: str
    payload: str
    rationale: str


class AdaptiveProbes(BaseModel):
    probes: list[AdaptiveProbe] = Field(default_factory=list)


@node("probe_generate")
async def probe_generate(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    ps = load_probe_set()
    rnd = int(state.get("probe_round", 0))
    max_adaptive = int(limits().get("probe", {}).get("max_adaptive_per_round", 4))
    usage: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    probes: list[dict[str, Any]] = []
    if rnd == 0:
        probes = [{"probe_id": p.id, "category": p.category, "adaptive": False} for p in ps.probes]
    llm = deps.llm("probe_generate", state.get("eval_config"))
    if llm is not None:
        model, name = llm
        manifest = state.get("manifest", {})
        tools = [
            {"name": t["name"], "description": t.get("description", ""), "side_effect": t.get("side_effect")}
            for t in manifest.get("tools", [])
        ]
        focus = state.get("uncertain_probe_ids") or [p.id for p in ps.probes if p.category != "BENIGN_CONTROL"]
        bases = [
            {"probe_id": p.id, "category": p.category, "goal": p.success.model_dump(), "example": p.turns[-1]}
            for p in ps.probes
            if p.id in focus
        ]
        try:
            out = await structured_call(
                model,
                get_prompt("probe_generate"),
                AdaptiveProbes,
                node="probe_generate",
                model_name=name,
                budget=deps.budget,
                usage=usage,
                tools=tools,
                bases=bases,
                max_n=max_adaptive,
                round=rnd,
            )
            for i, a in enumerate(out.probes[:max_adaptive]):
                if ps.get(a.base_probe_id) is None:
                    continue
                probes.append(
                    {
                        "probe_id": f"{a.base_probe_id}-A{rnd}{i + 1}",
                        "base_probe_id": a.base_probe_id,
                        "payload": a.payload,
                        "category": ps.get(a.base_probe_id).category,  # type: ignore[union-attr]
                        "adaptive": True,
                        "rationale": a.rationale,
                    }
                )
        except AppError as e:
            errors.append(node_error("probe_generate", e))
    await deps.event(
        state,
        "probe.generated",
        {"round": rnd, "count": len(probes), "adaptive": [p["probe_id"] for p in probes if p["adaptive"]]},
        node="probe_generate",
    )
    return {"probes": probes, "token_usage": usage, "errors": errors}
