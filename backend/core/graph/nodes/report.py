"""report: 전체 보고서 JSON + Markdown 생성, save_report 저장(서버 재마스킹), 대상 락 해제."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

from langchain_core.runnables import RunnableConfig

from core.graph.deps import get_deps
from core.graph.nodes._base import node
from core.redis.locks import release_target_lock
from core.schemas import SEVERITY_ORDER

SEV_KO = {"CRITICAL": "치명", "HIGH": "높음", "MEDIUM": "중간", "LOW": "낮음", "INFO": "참고"}


def build_report(state: dict[str, Any], db_findings: list[dict[str, Any]]) -> dict[str, Any]:
    status_of = {f["finding_id"]: f["status"] for f in db_findings}
    findings = []
    for f in state.get("correlated", []):
        ev = (f.get("evidence") or [{}])[0]
        findings.append(
            {
                "finding_id": f.get("finding_id"),
                "title": f["title"],
                "severity": f["severity"],
                "category": f["category"],
                "source": f["source"],
                "rule_id": f.get("rule_id"),
                "confidence": f["confidence"],
                "status": status_of.get(f.get("finding_id") or "", "OPEN"),
                "verdict": f.get("verdict"),
                "location": (
                    f"{ev.get('file_path')}:{ev.get('line_start')}" if ev.get("file_path") else ev.get("probe_id")
                ),
                "standard_refs": f.get("standard_refs", []),
                "reasoning_summary": f.get("reasoning_summary", ""),
                "tools_used": f.get("tools_used", []),
                "fix_hint": f.get("fix_hint"),
            }
        )
    findings.sort(key=lambda x: (-SEVERITY_ORDER[x["severity"]], -x["confidence"]))
    usage = state.get("token_usage", [])
    probes = [
        {
            k: r.get(k)
            for k in ("probe_id", "category", "attempts", "successes", "asr", "judged_by", "adaptive", "error")
        }
        for r in state.get("probe_results", [])
    ]
    return {
        "audit_id": state["audit_id"],
        "generated_at": datetime.now(UTC).isoformat(),
        "eval_config": state.get("eval_config"),
        "targets": [
            {"target_id": t["target_id"], "type": t["type"], "name": t["name"]} for t in state.get("targets", [])
        ],
        "summary": {
            "total": len(findings),
            "by_severity": dict(Counter(f["severity"] for f in findings)),
            "by_source": dict(Counter(f["source"] for f in findings)),
            "by_status": dict(Counter(f["status"] for f in findings)),
            "vulnerable_probes": sum(
                1 for p in probes if (p["successes"] or 0) > 0 and p["category"] != "BENIGN_CONTROL"
            ),
        },
        "risk_paths": state.get("risk_paths", []),
        "findings": findings,
        "probes": probes,
        "approval": state.get("approval"),
        "accepted_risks": state.get("accepted_risks", []),
        "fix_proposals": [
            {k: p.get(k) for k in ("proposal_id", "kind", "finding_ids", "branch", "pr_url")}
            for p in state.get("fix_proposals", [])
        ],
        "merge_status": state.get("merge_status"),
        "verify_result": state.get("verify_result"),
        "retry_count": state.get("retry_count", 0),
        "tokens": {
            "input": sum(u.get("in", 0) for u in usage),
            "output": sum(u.get("out", 0) for u in usage),
            "usd": round(sum(u.get("usd", 0) for u in usage), 6),
        },
        "errors": state.get("errors", []),
    }


def render_md(r: dict[str, Any]) -> str:
    s = r["summary"]
    lines = [
        f"# 보안 감사 보고서 — {r['audit_id'][:8]}",
        "",
        f"- 생성: {r['generated_at']}",
        f"- 대상: {', '.join(t['name'] for t in r['targets'])}",
        f"- Finding: 총 {s['total']}건 ("
        + ", ".join(f"{SEV_KO[k]} {s['by_severity'].get(k, 0)}" for k in SEVERITY_ORDER if s["by_severity"].get(k))
        + ")",
        f"- 취약 probe: {s['vulnerable_probes']}개",
        "",
    ]
    if r["risk_paths"]:
        lines += ["## 결합 위험", ""]
        lines += [
            f"- **[{p['severity']}] {p['title']}** ({p['rule_id']}) — {p['explanation']}" for p in r["risk_paths"]
        ]
        lines.append("")
    lines += ["## Finding 목록", "", "| 심각도 | 제목 | 위치 | 상태 | 근거 표준 |", "|---|---|---|---|---|"]
    for f in r["findings"]:
        lines.append(
            f"| {f['severity']} | {f['title']} | `{f['location']}` | {f['status']} | "
            f"{', '.join(f['standard_refs'][:3])} |"
        )
    if r["probes"]:
        lines += ["", "## 레드팀 probe", "", "| probe | 카테고리 | 성공/시도 | ASR |", "|---|---|---|---|"]
        lines += [
            f"| {p['probe_id']} | {p['category']} | {p['successes']}/{p['attempts']} | {p['asr'] or 0:.0%} |"
            for p in r["probes"]
        ]
    if r.get("approval"):
        a = r["approval"]
        lines += ["", "## 승인", "", f"- {a.get('decision')} by {a.get('decided_by')} — {a.get('comment') or ''}"]
    if r["fix_proposals"]:
        lines += ["", "## 수정·PR", ""] + [
            f"- {p['kind']}: `{p.get('branch')}` ({p.get('pr_url')})" for p in r["fix_proposals"]
        ]
    if r.get("verify_result"):
        v = r["verify_result"]
        lines += [
            "",
            "## 재검증",
            "",
            f"- 해결 {len(v['resolved'])}건, 미해결 {len(v['unresolved'])}건, "
            f"신규 {len(v['new'])}건 (재시도 {r['retry_count']}회)",
        ]
    lines += ["", "## 상세", ""]
    for f in r["findings"]:
        lines += [
            f"### [{f['severity']}] {f['title']}",
            "",
            f"- 위치: `{f['location']}`  ·  규칙: {f['rule_id']}  ·  신뢰도 {f['confidence']:.2f}",
            f"- 판단 근거: {f['reasoning_summary']}",
            f"- 사용 도구: {', '.join(f['tools_used'])}",
        ]
        if f.get("fix_hint"):
            lines.append(f"- 조치: {f['fix_hint']}")
        lines.append("")
    lines += ["---", f"토큰 in {r['tokens']['input']} / out {r['tokens']['output']}, 비용 ${r['tokens']['usd']}"]
    return "\n".join(lines)


@node("report")
async def report(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    db_findings = await deps.call(state, "report", "get_findings", audit_id=state["audit_id"])
    rep = build_report(state, db_findings)
    res = await deps.call(
        state, "report", "save_report", audit_id=state["audit_id"], report_json=rep, report_md=render_md(rep)
    )
    for t in state.get("targets", []):
        await release_target_lock(t["target_id"], state["audit_id"])
    await deps.event(state, "report.saved", {"report_id": res["report_id"], "summary": rep["summary"]}, node="report")
    return {"report_id": res["report_id"]}
