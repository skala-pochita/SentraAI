"""수정본 재업로드 비교: 기준 감사와 이번 감사의 Finding·probe 결과를 버전 간 키로 맞춰 본다.

순수 함수만 둔다 (DB·네트워크 없음). 입력은 두 감사의 스냅샷:
    {"audit_id", "mode", "created_at", "version", "findings": [Finding 행 dict], "report": report_json}

판정 원칙: 이번 감사가 해당 범위를 실제로 실행했다는 근거가 없으면 '해결'로 세지 않고 UNVERIFIED(확인 불가).
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from core.schemas import normalize_snippet

STATUS_KO = {
    "NEW": "신규 발견",
    "PERSISTING": "여전히 존재",
    "RESOLVED": "재검증에서 해소·차단",
    "BLOCKED": "재검증에서 차단",
    "UNVERIFIED": "확인 불가",
    "SAFE": "변화 없음 (양쪽 모두 차단)",
}
CODE_NODES = {"code_static_scan", "code_evidence_check"}
LLM_NODES = {"code_investigate"}
AGENT_STATIC_NODES = {"agent_static_check"}
ATTACK_NODES = {"prepare_sandbox", "probe_generate", "probe_run", "probe_judge"}
BENIGN = "BENIGN_CONTROL"

Key = tuple[str, ...]


# ---------------- 실행 범위 ----------------


def _verification(report: dict[str, Any]) -> str:
    """업로드 에이전트는 보고서의 모델 검증 상태, 내장 샘플은 기존 엔드포인트(EXTERNAL)."""
    marks = {c.get("model_verification") or "UNVERIFIED" for c in report.get("agent_coverage", [])}
    if not marks:
        return "EXTERNAL"
    return marks.pop() if len(marks) == 1 else "MIXED"


def scope(snap: dict[str, Any]) -> dict[str, Any]:
    """이번 감사가 무엇을 실제로 실행했는지. 근거가 없으면 False."""
    report = snap.get("report") or {}
    mode = snap.get("mode", "full")
    types = {t.get("type") for t in report.get("targets", [])}
    failed = {e.get("node") for e in report.get("errors", [])}
    coverage = report.get("agent_coverage", [])
    probes = report.get("probes", [])
    attacks = [c.get("attacks") for c in coverage]
    return {
        "code": mode in ("full", "code") and "repo" in types and not failed & CODE_NODES,
        "llm": int((report.get("tokens") or {}).get("input") or 0) > 0 and not failed & LLM_NODES,
        "agent_static": mode in ("full", "agent") and "agent" in types and not failed & AGENT_STATIC_NODES,
        "attacks": mode in ("full", "agent")
        and "agent" in types
        and bool(probes)
        and all(a == "COMPLETED" for a in attacks)
        and not failed & ATTACK_NODES,
        "verification": _verification(report),
    }


# ---------------- probe ----------------


def _probe_totals(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """적응형 변형(P03-A11 등)은 기준 probe 로 합친다. 예전 보고서에는 uncertain 이 없을 수 있다."""
    out: dict[str, dict[str, Any]] = {}
    for p in report.get("probes", []):
        pid = p.get("base_probe_id") or p["probe_id"]
        t = out.setdefault(
            pid, {"category": p["category"], "attempts": 0, "successes": 0, "uncertain": 0, "error": None}
        )
        t["attempts"] += int(p.get("attempts") or 0)
        t["successes"] += int(p.get("successes") or 0)
        t["uncertain"] += int(p.get("uncertain") or 0)
        t["error"] = t["error"] or p.get("error")
    return out


def _ran(t: dict[str, Any] | None) -> bool:
    return t is not None and not t["error"] and t["attempts"] > 0


def _benign_state(totals: dict[str, dict[str, Any]]) -> str:
    """정상 대조군: SUCCESS = 정상 응답이 통과. 하나라도 막히면 FAIL."""
    rows = [t for t in totals.values() if t["category"] == BENIGN]
    if not rows or not all(_ran(t) for t in rows):
        return "NOT_RUN"
    return "PASS" if all(t["successes"] == t["attempts"] for t in rows) else "FAIL"


def _probe_unverified_reason(cur: dict[str, Any] | None, sc: dict[str, Any], same_mode: bool) -> str | None:
    if not same_mode:
        return "두 감사의 실행 모드(실제 LLM·모의)가 달라 공격 결과를 비교할 수 없습니다."
    if not sc["attacks"]:
        return "이번 감사에서 공격 검사가 완료되지 않았습니다 (미실행·오류·미검증)."
    if not _ran(cur):
        return "같은 공격이 이번 감사에서 실행되지 않았거나 오류로 끝났습니다."
    assert cur is not None
    if cur["successes"] == 0 and cur["uncertain"] > 0:
        return "판정이 불확실한 시도가 남아 있습니다."
    return None


def compare_probes(
    base_report: dict[str, Any], cur_report: dict[str, Any], sc: dict[str, Any], same_mode: bool
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    base, cur = _probe_totals(base_report), _probe_totals(cur_report)
    benign = {"baseline": _benign_state(base), "current": _benign_state(cur)}
    rows = []
    for pid in sorted(set(base) | set(cur)):
        b, c = base.get(pid), cur.get(pid)
        category = (b or c or {}).get("category", "")
        if category == BENIGN:
            continue
        was = bool(b and b["successes"] > 0)
        reason = _probe_unverified_reason(c, sc, same_mode)
        now = bool(c and c["successes"] > 0)
        if now and same_mode:
            status, reason = ("PERSISTING" if was else "NEW"), None
        elif reason:
            status = "UNVERIFIED"
        elif benign["current"] != "PASS":
            status = "UNVERIFIED"
            reason = "정상 대조군 요청이 통과하지 못해, 공격이 막힌 이유가 기능 손상일 수 있습니다."
        else:
            status = "BLOCKED" if was else "SAFE"
        rows.append(
            {
                "probe_id": pid,
                "category": category,
                "status": status,
                "label": STATUS_KO[status],
                "reason": reason,
                "baseline": {k: b[k] for k in ("attempts", "successes")} if b else None,
                "current": {k: c[k] for k in ("attempts", "successes")} if c else None,
            }
        )
    return rows, benign


# ---------------- Finding ----------------


def _primary(f: dict[str, Any]) -> dict[str, Any]:
    return (f.get("evidence") or [{}])[0]


def _path(f: dict[str, Any]) -> str:
    return str(_primary(f).get("file_path") or "").lstrip("./")


def keys(f: dict[str, Any]) -> tuple[Key, Key]:
    """(정확 키, 완화 키). 대상 ID·라인 번호는 버전마다 달라지므로 쓰지 않는다."""
    rule = f.get("rule_id") or ""
    if f["source"] == "correlation" or rule.startswith("PROBE-"):
        k = (f["source"], f["category"], rule)
        return k, k
    loose = (f["source"], f["category"], rule, _path(f))
    return loose + (normalize_snippet(_primary(f).get("snippet_masked")),), loose


def _match(base: list[dict[str, Any]], cur: list[dict[str, Any]]) -> tuple[list[tuple[Any, Any]], list[Any]]:
    """정확 키로 먼저, 남은 것끼리 완화 키로 1:1 연결. (쌍 목록, 이번에만 있는 것)."""
    pairs: list[tuple[Any, Any]] = []
    left = list(base)
    pool = list(cur)
    for which in (0, 1):
        index: dict[Key, list[dict[str, Any]]] = defaultdict(list)
        for f in pool:
            index[keys(f)[which]].append(f)
        rest = []
        for f in left:
            bucket = index.get(keys(f)[which])
            if bucket:
                g = bucket.pop(0)
                pool.remove(g)
                pairs.append((f, g))
            else:
                rest.append(f)
        left = rest
    pairs += [(f, None) for f in left]
    return pairs, pool


def _gone_status(f: dict[str, Any], sc: dict[str, Any], probe_status: dict[str, str]) -> tuple[str, str | None]:
    """기준 감사에만 있던 Finding: 이번 감사가 그 범위를 실행했을 때만 해소로 본다."""
    rule = f.get("rule_id") or ""
    if rule.startswith("PROBE-"):
        st = probe_status.get(rule.removeprefix("PROBE-"), "UNVERIFIED")
        if st in ("BLOCKED", "SAFE"):
            return "RESOLVED", None
        return "UNVERIFIED", "같은 공격의 재검증 결과를 확인하지 못했습니다."
    if f["source"] == "correlation":
        if sc["code"] and sc["agent_static"] and sc["attacks"]:
            return "RESOLVED", None
        return "UNVERIFIED", "결합 위험을 다시 판단하려면 코드·에이전트·공격 검사가 모두 완료되어야 합니다."
    if f["source"] == "agent":
        return ("RESOLVED", None) if sc["agent_static"] else ("UNVERIFIED", "에이전트 권한 검사를 실행하지 않았습니다.")
    if not sc["code"]:
        return "UNVERIFIED", "이번 감사에서 코드 검사를 실행하지 않았습니다."
    if not rule and not sc["llm"]:
        return "UNVERIFIED", "LLM 조사로 찾은 항목인데 이번 감사에서는 LLM 조사가 실행되지 않았습니다."
    return "RESOLVED", None


def _row(status: str, reason: str | None, base: dict[str, Any] | None, cur: dict[str, Any] | None) -> dict[str, Any]:
    f = cur or base
    assert f is not None
    ev = _primary(f)
    return {
        "status": status,
        "label": STATUS_KO[status],
        "reason": reason,
        "severity": f["severity"],
        "title": f["title"],
        "category": f["category"],
        "source": f["source"],
        "rule_id": f.get("rule_id"),
        "location": f"{ev['file_path']}:{ev.get('line_start')}" if ev.get("file_path") else ev.get("probe_id"),
        "baseline_finding_id": base["id"] if base else None,
        "current_finding_id": cur["id"] if cur else None,
        "baseline_severity": base["severity"] if base else None,
    }


def _live(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [f for f in rows if f.get("status") != "FALSE_POSITIVE"]


def compare(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    sc = scope(current)
    base_sc = scope(baseline)
    same_mode = base_sc["verification"] == sc["verification"] and sc["verification"] != "MIXED"
    probes, benign = compare_probes(baseline.get("report") or {}, current.get("report") or {}, sc, same_mode)
    probe_status = {p["probe_id"]: p["status"] for p in probes}

    pairs, only_new = _match(_live(baseline["findings"]), _live(current["findings"]))
    findings = []
    for b, c in pairs:
        if c is not None:
            findings.append(_row("PERSISTING", None, b, c))
        else:
            findings.append(_row(*_gone_status(b, sc, probe_status), b, None))
    findings += [_row("NEW", None, None, c) for c in only_new]
    order = {"NEW": 0, "PERSISTING": 1, "UNVERIFIED": 2, "RESOLVED": 3}
    sev = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
    findings.sort(key=lambda r: (order[r["status"]], sev.get(r["severity"], 5), r["title"]))

    notes = []
    if not same_mode:
        notes.append(
            f"실행 모드가 다릅니다 (기준 {base_sc['verification']} · 이번 {sc['verification']}). 공격 결과는 확인 불가로 표시합니다."
        )
    elif sc["verification"] == "MOCK":
        notes.append("두 감사 모두 모의(mock) 실행입니다. 공격 차단은 실제 LLM 방어 성능을 뜻하지 않습니다.")
    if benign["current"] == "FAIL":
        notes.append("이번 감사에서 정상 대조군 요청이 통과하지 못했습니다. 공격 차단을 해결로 세지 않습니다.")
    return {
        "baseline": _meta(baseline, base_sc),
        "current": _meta(current, sc),
        "summary": {s: sum(1 for f in findings if f["status"] == s) for s in order},
        "probe_summary": {s: sum(1 for p in probes if p["status"] == s) for s in STATUS_KO if s != "RESOLVED"},
        "findings": findings,
        "probes": probes,
        "benign": benign,
        "scope": {k: v for k, v in sc.items() if k != "verification"},
        "notes": notes,
    }


def _meta(snap: dict[str, Any], sc: dict[str, Any]) -> dict[str, Any]:
    return {
        "audit_id": snap["audit_id"],
        "created_at": snap.get("created_at"),
        "version": snap.get("version"),
        "mode": snap.get("mode"),
        "model_verification": sc["verification"],
    }


# ---------------- Markdown ----------------


def render_md(r: dict[str, Any]) -> str:
    b, c, s = r["baseline"], r["current"], r["summary"]
    lines = [
        f"# 수정 전후 비교 — {c['audit_id'][:8]} (기준 {b['audit_id'][:8]})",
        "",
        f"- 기준: v{b.get('version') or '?'} · {b.get('created_at')} · 모델 검증 {b['model_verification']}",
        f"- 이번: v{c.get('version') or '?'} · {c.get('created_at')} · 모델 검증 {c['model_verification']}",
        f"- Finding: 신규 {s['NEW']} · 여전히 존재 {s['PERSISTING']} · 해소 {s['RESOLVED']} · 확인 불가 {s['UNVERIFIED']}",
        f"- 정상 대조군: 기준 {r['benign']['baseline']} · 이번 {r['benign']['current']}",
        "- 확인 불가는 해결로 세지 않습니다. 감사기는 원본을 수정하지 않았습니다.",
        "",
    ]
    lines += [f"> {n}" for n in r["notes"]] + ([""] if r["notes"] else [])
    if r["probes"]:
        lines += [
            "## 동일 공격 재검증",
            "",
            "| probe | 카테고리 | 기준 성공/시도 | 이번 성공/시도 | 판정 |",
            "|---|---|---|---|---|",
        ]
        for p in r["probes"]:
            bb = f"{p['baseline']['successes']}/{p['baseline']['attempts']}" if p["baseline"] else "-"
            cc = f"{p['current']['successes']}/{p['current']['attempts']}" if p["current"] else "-"
            lines.append(f"| {p['probe_id']} | {p['category']} | {bb} | {cc} | {p['label']} |")
        lines.append("")
    lines += ["## Finding 비교", "", "| 판정 | 심각도 | 제목 | 위치 | 사유 |", "|---|---|---|---|---|"]
    for f in r["findings"]:
        lines.append(f"| {f['label']} | {f['severity']} | {f['title']} | `{f['location']}` | {f['reason'] or ''} |")
    return "\n".join(lines) + "\n"
