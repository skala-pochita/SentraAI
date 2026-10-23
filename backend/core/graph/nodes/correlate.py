"""correlate: 7.4 결합 위험 규칙 적용, 중복 병합, 심각도 재산정, Finding DB 저장 (create_finding, 멱등)."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from core.attack_paths import correlate_paths, correlation_gaps
from core.graph.deps import get_deps
from core.graph.nodes._base import node
from core.path_schema import AttackPath
from core.schemas import SEVERITY_ORDER, FindingDraft


def _key(f: FindingDraft) -> tuple[str, str, str, int]:
    ev = f.primary
    return (
        f.target_id,
        f.category,
        (ev.file_path or ev.probe_id or "") if ev else "",
        (ev.line_start or 0) if ev else 0,
    )


def dedupe(findings: list[FindingDraft]) -> list[FindingDraft]:
    """같은 대상·카테고리·파일에서 3줄 이내면 하나로 병합 (높은 심각도·근거 합침)."""
    out: list[FindingDraft] = []
    for f in sorted(findings, key=lambda x: (-SEVERITY_ORDER[x.severity], -x.confidence)):
        t, c, p, line = _key(f)
        dup = next(
            (
                o
                for o in out
                if _key(o)[:3] == (t, c, p) and abs(_key(o)[3] - line) <= 3 and p and o.subject == f.subject
            ),
            None,
        )
        if dup is None:
            out.append(f)
            continue
        dup.evidence.extend(e for e in f.evidence if e not in dup.evidence)
        dup.tools_used = sorted(set(dup.tools_used) | set(f.tools_used))
        dup.standard_refs = sorted(set(dup.standard_refs) | set(f.standard_refs))
    return out


def apply_correlation(
    findings: list[FindingDraft],
    probe_results: list[dict[str, Any]],
    targets: list[dict[str, Any]],
    plan: dict[str, Any],
) -> list[FindingDraft]:
    return correlate_paths(findings, probe_results, targets, plan)


@node("correlate")
async def correlate(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    raw = [FindingDraft.model_validate(f) for f in state.get("code_findings", []) + state.get("agent_findings", [])]
    kept = dedupe([f for f in raw if f.verdict != "FALSE_POSITIVE"])
    corr = apply_correlation(kept, state.get("probe_results", []), state.get("targets", []), state.get("plan", {}))
    final = kept + corr
    # 저장: 구성 Finding 먼저 (correlation 의 component_ids 를 finding_id 로 치환)
    id_map: dict[str, str] = {}
    for f in final:
        if f.source == "correlation":
            f.component_ids = [id_map.get(c, c) for c in f.component_ids]
            if f.attack_path:
                for reason in f.attack_path.code_reasons:
                    if reason.ref.finding_id:
                        reason.ref.finding_id = id_map.get(reason.ref.finding_id, reason.ref.finding_id)
                for item in [*f.attack_path.steps, *f.attack_path.edges]:
                    for ref in item.evidence_refs:
                        if ref.finding_id:
                            ref.finding_id = id_map.get(ref.finding_id, ref.finding_id)
        res = await deps.call(state, "correlate", "create_finding", audit_id=state["audit_id"], finding=f.model_dump())
        f.finding_id = res["finding_id"]
        if res.get("path_payload"):
            f.attack_path = AttackPath.model_validate(res["path_payload"])
        id_map[f.temp_id] = f.finding_id
    final.sort(key=lambda f: (-SEVERITY_ORDER[f.severity], -f.confidence))
    risk_paths = [
        {
            "rule_id": f.rule_id,
            "severity": f.severity,
            "title": f.title,
            "finding_id": f.finding_id,
            "component_ids": f.component_ids,
            "explanation": f.reasoning_summary,
            "payload": f.attack_path.model_dump() if f.attack_path else None,
        }
        for f in final
        if f.source == "correlation"
    ]
    gaps = correlation_gaps(state.get("probe_results", []), state.get("targets", []), state.get("plan", {}))
    needs = any(f.severity == "CRITICAL" for f in final)
    counts: dict[str, int] = {}
    for f in final:
        counts[f.severity] = counts.get(f.severity, 0) + 1
    await deps.event(
        state,
        "findings.saved",
        {"count": len(final), "by_severity": counts, "risk_paths": [r["title"] for r in risk_paths]},
        node="correlate",
    )
    return {
        "correlated": [f.model_dump() for f in final],
        "risk_paths": risk_paths,
        "needs_approval": needs,
        "correlation_gaps": gaps,
    }
