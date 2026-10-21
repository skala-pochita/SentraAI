"""probe_judge 의 LLM 판정 경로 (structured_call 대역): 판정 불가 시도만 판정, 실패는 다음 라운드로."""

from __future__ import annotations

from typing import Any

import pytest

from core.errors import AppError, ErrorCode
from core.graph.deps import Deps
from core.graph.nodes import probe_judge as pj
from core.mcp_client import inprocess_gateway


def result(pid: str, verdicts: list[str], successes: int = 0) -> dict[str, Any]:
    return {
        "probe_id": pid,
        "category": "SYSTEM_PROMPT_LEAK",
        "attempts": len(verdicts),
        "successes": successes,
        "asr": successes / len(verdicts),
        "uncertain": verdicts.count("UNDETERMINED"),
        "judged_by": "deterministic",
        "run_ids": ["r1"],
        "transcripts": [{"verdict": v, "reply_masked": "CANA****", "tool_calls": []} for v in verdicts],
    }


async def test_llm_judges_only_undetermined_and_loops_on_failure(
    fake_redis: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    async def fake_call(model: Any, prompt: Any, schema: Any, **kw: Any) -> Any:
        calls.append(kw["transcript"])
        if len(calls) == 3:
            raise AppError(ErrorCode.UPSTREAM_TIMEOUT, "slow")
        return schema(attack_succeeded=len(calls) == 1, reasoning="r")

    monkeypatch.setattr(pj, "structured_call", fake_call)
    deps = Deps(gateway=inprocess_gateway(), emit_events=False, llm=lambda node, cfg: (object(), "fake-model"))
    state = {
        "audit_id": None,
        "target": {"target_id": "agent-1"},
        "probe_results": [
            result("P01", ["UNDETERMINED", "BLOCKED", "UNDETERMINED"]),
            result("P03", ["UNDETERMINED"]),
            result("NOPE", ["UNDETERMINED"]),  # 데이터셋에 없는 probe 는 판정 불가로 남는다
            result("P05", ["SUCCESS"], successes=1) | {"category": "TOOL_MISUSE"},
        ],
        "agent_findings": [],
        "probe_round": 0,
    }
    out = await pj.probe_judge(state, {"configurable": {"deps": deps}})
    assert len(calls) == 3 and all("<untrusted" in c for c in calls)  # 판정 불가 3건만, untrusted 로 감싸서
    p01, p03 = out["probe_results"][0], out["probe_results"][1]
    assert p01["successes"] == 1 and p01["judged_by"] == "llm" and p01["asr"] == round(1 / 3, 3)
    assert p03["uncertain"] == 1 and out["errors"][0]["code"] == "UPSTREAM_TIMEOUT"
    assert set(out["uncertain_probe_ids"]) == {"P03", "NOPE"} and out["probe_round"] == 1
    assert {f["rule_id"] for f in out["agent_findings"]} == {"PROBE-P01", "PROBE-P05"}

    # 마지막 라운드면 더 돌지 않는다
    again = await pj.probe_judge(state | out | {"probe_round": 1}, {"configurable": {"deps": deps}})
    assert again["uncertain_probe_ids"] == []


async def test_probe_generate_adds_bounded_adaptive_variants(
    fake_redis: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    from core.graph.nodes import probe_generate as pg

    seen: dict[str, Any] = {}

    async def fake_call(model: Any, prompt: Any, schema: Any, **kw: Any) -> Any:
        seen.update(kw)
        items = [{"base_probe_id": b, "payload": f"try {b}", "rationale": "r"} for b in ["P01", "NOPE", "P03"] * 3]
        return schema(probes=items)

    monkeypatch.setattr(pg, "structured_call", fake_call)
    deps = Deps(gateway=inprocess_gateway(), emit_events=False, llm=lambda node, cfg: (object(), "fake-model"))
    state = {"audit_id": None, "target": {"target_id": "a"}, "manifest": {"tools": [{"name": "delete_customer"}]}}
    out = await pg.probe_generate(state | {"probe_round": 1, "uncertain_probe_ids": ["P01"]}, {"configurable": {"deps": deps}})
    adaptive = [p for p in out["probes"] if p["adaptive"]]
    assert [p["probe_id"] for p in adaptive] == ["P01-A11", "P03-A13", "P01-A14"]  # 상한 4개 중 없는 base 제외
    assert [b["probe_id"] for b in seen["bases"]] == ["P01"] and seen["tools"][0]["name"] == "delete_customer"
    assert not any(not p["adaptive"] for p in out["probes"])  # 1라운드 이후엔 기본 probe 를 다시 넣지 않는다

    async def fail(*_: Any, **__: Any) -> Any:
        raise AppError(ErrorCode.BUDGET_EXCEEDED, "budget")

    monkeypatch.setattr(pg, "structured_call", fail)
    first = await pg.probe_generate(state | {"probe_round": 0}, {"configurable": {"deps": deps}})
    assert first["errors"][0]["code"] == "BUDGET_EXCEEDED" and len(first["probes"]) == 12
