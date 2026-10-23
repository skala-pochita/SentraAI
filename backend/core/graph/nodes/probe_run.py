"""probe_run: 각 probe 를 N회 실행 (동시성 3). retryable 실패가 노드 안 재시도 후에도 남으면 Kafka 재시도로."""

from __future__ import annotations

import asyncio
from typing import Any

from langchain_core.runnables import RunnableConfig

from core.errors import AppError, RetryableNodeError
from core.graph.deps import get_deps, node_error
from core.graph.nodes._base import node
from core.schemas import ProbeResult
from core.settings import get_settings
from harness.budget import limits


@node("probe_run")
async def probe_run(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    tid = state["target"]["target_id"]
    cfg = limits().get("probe", {})
    attempts = int(get_settings().probe_attempts or cfg.get("attempts", 3))
    sem = asyncio.Semaphore(int(cfg.get("concurrency", 3)))
    errors: list[dict[str, Any]] = []
    done = 0

    async def one(p: dict[str, Any]) -> dict[str, Any]:
        nonlocal done
        async with sem:
            try:
                res = await deps.call(
                    state,
                    "probe_run",
                    "run_injection_probe",
                    target_id=tid,
                    probe_id=p.get("base_probe_id") or p["probe_id"],
                    payload=p.get("payload"),
                    attempts=attempts,
                )
            except RetryableNodeError:
                raise
            except AppError as e:
                errors.append(node_error("probe_run", e))
                return ProbeResult(
                    target_id=tid,
                    probe_id=p["probe_id"],
                    category=p["category"],
                    attempts=0,
                    successes=0,
                    asr=0,
                    judged_by="deterministic",
                    adaptive=p.get("adaptive", False),
                    base_probe_id=p.get("base_probe_id"),
                    error=str(e.code),
                ).model_dump()
            att = res["attempts"]
            succ = sum(1 for a in att if a["verdict"] == "SUCCESS")
            unc = sum(1 for a in att if a["verdict"] == "UNDETERMINED")
            tools_hit = sorted(
                {t["name"] for a in att if a["verdict"] == "SUCCESS" for t in a["tool_calls"] if t.get("executed")}
            )
            done += 1
            await deps.event(
                state,
                "probe.progress",
                {
                    "probe_id": p["probe_id"],
                    "successes": succ,
                    "attempts": len(att),
                    "done": done,
                    "total": len(probes),
                },
                node="probe_run",
            )
            return ProbeResult(
                target_id=tid,
                probe_id=p["probe_id"],
                category=res["category"],
                attempts=len(att),
                successes=succ,
                asr=round(succ / len(att), 3) if att else 0.0,
                judged_by="deterministic",
                run_ids=res["probe_run_ids"],
                uncertain=unc,
                adaptive=p.get("adaptive", False),
                base_probe_id=p.get("base_probe_id"),
                channel=res["channel"],
                tools_hit=tools_hit,
            ).model_dump() | {
                "runtime_mode": state.get("runtime", {}).get("mode", "unknown"),
                "round": state.get("probe_round", 0),
                "transcripts": [{**a, "target_id": tid, "judged_by": "deterministic"} for a in att],
            }

    probes = state.get("probes", [])
    results = await asyncio.gather(*(one(p) for p in probes))
    prev = [r for r in state.get("probe_results", []) if r["probe_id"] not in {p["probe_id"] for p in probes}]
    return {"probe_results": prev + list(results), "errors": errors}
