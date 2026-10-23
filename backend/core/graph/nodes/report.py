"""report: 전체 보고서 JSON + Markdown 생성, save_report 저장(서버 재마스킹), 대상 락 해제."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

from langchain_core.runnables import RunnableConfig

from core.compliance import build_compliance_safely, render_compliance, seal_report
from core.graph.deps import get_deps
from core.graph.nodes._base import node
from core.redis.locks import release_target_lock
from core.schemas import SEVERITY_ORDER
from core.settings import get_settings

SEV_KO = {"CRITICAL": "치명", "HIGH": "높음", "MEDIUM": "중간", "LOW": "낮음", "INFO": "참고"}


PROBE_FIELDS = ("probe_id", "category", "attempts", "successes", "asr", "judged_by", "adaptive", "error")


def _report_finding(f: dict[str, Any], status_of: dict[str, str]) -> dict[str, Any]:
    ev = (f.get("evidence") or [{}])[0]
    location = f"{ev.get('file_path')}:{ev.get('line_start')}" if ev.get("file_path") else ev.get("probe_id")
    return {
        "finding_id": f.get("finding_id"),
        "target_id": f.get("target_id"),
        "title": f["title"],
        "severity": f["severity"],
        "category": f["category"],
        "source": f["source"],
        "rule_id": f.get("rule_id"),
        "confidence": f["confidence"],
        "status": status_of.get(f.get("finding_id") or "", "OPEN"),
        "verdict": f.get("verdict"),
        "location": location,
        "standard_refs": f.get("standard_refs", []),
        "reasoning_summary": f.get("reasoning_summary", ""),
        "tools_used": f.get("tools_used", []),
        "fix_hint": f.get("fix_hint"),
    }


def _summary(findings: list[dict[str, Any]], probes: list[dict[str, Any]]) -> dict[str, Any]:
    vulnerable = sum(1 for p in probes if (p["successes"] or 0) > 0 and p["category"] != "BENIGN_CONTROL")
    return {
        "total": len(findings),
        "by_severity": dict(Counter(f["severity"] for f in findings)),
        "by_source": dict(Counter(f["source"] for f in findings)),
        "by_status": dict(Counter(f["status"] for f in findings)),
        "vulnerable_probes": vulnerable,
    }


def _coverage(state: dict[str, Any], probes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """계획(PLANNED)만 된 공격 검사는 실제 결과(오류 여부)로 바꿔 적는다."""
    attacks = "ERROR" if any(r.get("error") for r in probes) else "COMPLETED"
    return [dict(c, attacks=attacks) if c.get("attacks") == "PLANNED" else c for c in state.get("agent_coverage", [])]


def build_report(state: dict[str, Any], db_findings: list[dict[str, Any]]) -> dict[str, Any]:
    status_of = {f["finding_id"]: f["status"] for f in db_findings}
    findings = [_report_finding(f, status_of) for f in state.get("correlated", [])]
    findings.sort(key=lambda x: (-SEVERITY_ORDER[x["severity"]], -x["confidence"]))
    usage = state.get("token_usage", [])
    probes = [{k: r.get(k) for k in PROBE_FIELDS} for r in state.get("probe_results", [])]
    rep = {
        "audit_id": state["audit_id"],
        "sentra_build_sha": get_settings().sentra_build_sha,
        "generated_at": datetime.now(UTC).isoformat(),
        "eval_config": state.get("eval_config"),
        "targets": [
            {"target_id": t["target_id"], "type": t["type"], "name": t["name"]} for t in state.get("targets", [])
        ],
        "summary": _summary(findings, probes),
        "agent_coverage": _coverage(state, probes),
        "risk_paths": state.get("risk_paths", []),
        "findings": findings,
        "probes": probes,
        "tokens": {
            "input": sum(u.get("in", 0) for u in usage),
            "output": sum(u.get("out", 0) for u in usage),
            "usd": round(sum(u.get("usd", 0) for u in usage), 6),
        },
        "errors": state.get("errors", []),
        "compliance_profile": state.get("compliance_profile", {}),
        "audit_mode": state.get("mode", "full"),
    }
    rep["compliance"] = build_compliance_safely(rep, state.get("compliance_profile"))
    return seal_report(rep)


def render_md(r: dict[str, Any]) -> str:
    s = r["summary"]
    lines = [
        f"# 보안 감사 보고서 — {r['audit_id'][:8]}",
        "",
        "- 범위: 현재 소스의 문제점 분석 및 권장 조치. 원본 코드를 변경하지 않았습니다.",
        f"- 생성: {r['generated_at']}",
        f"- 대상: {', '.join(t['name'] for t in r['targets'])}",
        f"- Finding: 총 {s['total']}건 ("
        + ", ".join(f"{SEV_KO[k]} {s['by_severity'].get(k, 0)}" for k in SEVERITY_ORDER if s["by_severity"].get(k))
        + ")",
        f"- 취약 probe: {s['vulnerable_probes']}개",
        "",
    ]
    if r.get("agent_coverage"):
        lines += ["## 에이전트 검사 범위", ""]
        for c in r["agent_coverage"]:
            runtime = c.get("runtime", {})
            lines.append(
                f"- {c['target_id']}: 권한 검사 {c['static']}, 격리 실행 {runtime.get('status')}, 공격 검사 {c['attacks']}, 실행 모드 {runtime.get('mode', 'unknown')}, 모델 검증 {c.get('model_verification', 'UNVERIFIED')}. {c.get('reason', '')}"
            )
        lines.append("모의(mock) 에이전트의 도구 실행 결과는 실제 LLM의 인젝션 방어 성능을 뜻하지 않습니다.")
        lines.append("")
    if r["risk_paths"]:
        lines += ["## 결합 위험", ""]
        lines += [
            f"- **[{p['severity']}] {p['title']}** ({p['rule_id']}) — {p['explanation']}" for p in r["risk_paths"]
        ]
        lines.append("")
    if r.get("compliance"):
        lines += [render_compliance(r), ""]
    lines += ["## Finding 목록", "", "| 심각도 | 제목 | 위치 | 상태 | 근거 표준 |", "|---|---|---|---|---|"]
    for f in r["findings"]:
        lines.append(
            f"| {f['severity']} | {f['title']} | `{f['location']}` | {f['status']} | "
            f"{', '.join(f['standard_refs'][:3])} |"
        )
    if r["probes"]:
        lines += ["", "## 레드팀 probe", "", "| probe | 카테고리 | 성공/시도 | ASR |", "|---|---|---|---|"]
        for p in r["probes"]:
            value = (
                "미측정 (" + str(p.get("error") or "미실행") + ")"
                if p.get("error") or not p["attempts"]
                else f"{p['asr'] or 0:.0%}"
            )
            lines.append(f"| {p['probe_id']} | {p['category']} | {p['successes']}/{p['attempts']} | {value} |")
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
    if r.get("target_model_usage"):
        usage = r["target_model_usage"]
        lines += [
            "## 타깃 모델 사용량",
            "",
            f"- 요청 {usage.get('requests', 0)}회, 제공자 성공 {usage.get('successful_requests', 0)}회, 차감 토큰 {usage.get('charged_tokens', 0)}, 모델 {usage.get('model')}",
        ]
    lines += ["---", f"토큰 in {r['tokens']['input']} / out {r['tokens']['output']}, 비용 ${r['tokens']['usd']}"]
    return "\n".join(lines)


@node("report")
async def report(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    db_findings = await deps.call(state, "report", "get_findings", audit_id=state["audit_id"])
    rep = build_report(state, db_findings)
    from core.proxy_sessions import usage

    proxy_usage = await usage(state["audit_id"])
    rep["target_model_usage"] = proxy_usage
    for coverage in rep.get("agent_coverage", []):
        runtime = coverage.get("runtime", {})
        verified = runtime.get("requested_mode") == "live" and proxy_usage.get("successful_requests", 0) > 0
        coverage["model_verification"] = (
            "LIVE_VERIFIED" if verified else ("MOCK" if runtime.get("mode") == "mock" else "UNVERIFIED")
        )
        if runtime.get("requested_mode") == "live" and not verified:
            coverage["attacks"] = "UNVERIFIED"
            coverage["reason"] = "실제 제공자 호출 성공을 확인하지 못했습니다."

    # Finalize recommendations after model verification above.
    rep["compliance"] = build_compliance_safely(rep, state.get("compliance_profile"))
    rep = seal_report(rep)
    res = await deps.call(
        state, "report", "save_report", audit_id=state["audit_id"], report_json=rep, report_md=render_md(rep)
    )
    for t in state.get("targets", []):
        if t["type"] == "agent":
            from core.errors import AppError

            try:
                await deps.call(state, "report", "stop_agent_sandbox", target_id=t["target_id"])
            except AppError as exc:
                await deps.event(state, "sandbox.cleanup_failed", {"code": str(exc.code)}, node="report")
        await release_target_lock(t["target_id"], state["audit_id"])
    await deps.event(state, "report.saved", {"report_id": res["report_id"], "summary": rep["summary"]}, node="report")
    return {"report_id": res["report_id"]}
