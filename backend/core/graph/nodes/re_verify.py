"""re_verify: 병합 후 영향 대상 재스캔 + 관련 probe 재실행 (hardened 반영 확인), 해결 여부 판정."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from core.errors import AppError
from core.graph.deps import get_deps, node_error
from core.graph.nodes._base import node
from core.schemas import FindingDraft, normalize_snippet
from core.settings import get_settings


def _subject(f: FindingDraft) -> str:
    return f.title.split(": ", 1)[1] if ": " in f.title else ""


@node("re_verify")
async def re_verify(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    by_id = {
        f["finding_id"]: FindingDraft.model_validate(f) for f in state.get("correlated", []) if f.get("finding_id")
    }
    targets = [by_id[i] for i in state.get("fix_targets", []) if i in by_id]
    types = {t["target_id"]: t["type"] for t in state.get("targets", [])}
    errors: list[dict[str, Any]] = []
    code_hits: dict[str, list[dict[str, Any]]] = {}
    agent_hits: dict[str, list[dict[str, Any]]] = {}
    for tid in {f.target_id for f in targets}:
        if types.get(tid) == "repo":
            code_hits[tid] = (await deps.call(state, "re_verify", "run_static_scan", target_id=tid))["hits"]
        elif types.get(tid) == "agent":
            agent_hits[tid] = (await deps.call(state, "re_verify", "run_agent_rule_check", target_id=tid))["hits"]

    resolved: list[str] = []
    unresolved: list[str] = []
    probe_reruns: dict[str, int] = {}
    for f in targets:
        if f.source == "correlation":
            continue
        ok = True
        if (f.rule_id or "").startswith("PROBE-"):
            base = (f.rule_id or "")[len("PROBE-") :]
            try:
                res = await deps.call(
                    state,
                    "re_verify",
                    "run_injection_probe",
                    target_id=f.target_id,
                    probe_id=base,
                    attempts=int(get_settings().probe_attempts),
                )
                wins = sum(1 for a in res["attempts"] if a["verdict"] == "SUCCESS")
            except AppError as e:
                errors.append(node_error("re_verify", e))
                wins = 1
            probe_reruns[base] = wins
            ok = wins == 0
        elif f.source == "code":
            snip = normalize_snippet(f.primary.snippet_masked if f.primary else "")
            path = f.primary.file_path if f.primary else None
            ok = not any(
                h["rule_id"] == f.rule_id and h["path"] == path and normalize_snippet(h["snippet_masked"]) == snip
                for h in code_hits.get(f.target_id, [])
            )
        else:
            subj = _subject(f)
            ok = not any(
                h["category"] == f.category
                and (h["extra"].get("tool") or h["extra"].get("env") or h["extra"].get("target")) == subj
                for h in agent_hits.get(f.target_id, [])
            )
        (resolved if ok else unresolved).append(f.finding_id or "")
    for f in targets:
        if f.source == "correlation":
            parts = set(f.component_ids)
            (resolved if parts and parts <= set(resolved) else unresolved).append(f.finding_id or "")

    known = {(normalize_snippet(f.primary.snippet_masked), f.rule_id) for f in by_id.values() if f.primary}
    new = [
        f"{h['rule_id']}@{h['path']}:{h['line']}"
        for hs in code_hits.values()
        for h in hs
        if (normalize_snippet(h["snippet_masked"]), h["rule_id"]) not in known and h["severity"] != "INFO"
    ]
    for fid in resolved:
        await deps.call(
            state,
            "re_verify",
            "update_finding_status",
            finding_id=fid,
            status="RESOLVED",
            reason="re-verified after merge",
        )
    retry = int(state.get("retry_count", 0)) + (1 if unresolved else 0)
    result = {"resolved": resolved, "unresolved": unresolved, "new": new, "probe_reruns": probe_reruns}
    await deps.event(state, "verify.result", {**result, "retry_count": retry}, node="re_verify")
    return {"verify_result": result, "retry_count": retry, "errors": errors}
