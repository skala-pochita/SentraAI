"""수정본 재업로드 비교: 버전 간 이슈 매칭과 '확인 불가는 해결로 세지 않는다' 규칙."""

from __future__ import annotations

from typing import Any

from core.audit_compare import compare, render_md

N = 0


def finding(
    category: str,
    rule: str | None,
    path: str = "",
    snippet: str = "",
    *,
    source: str = "code",
    probe: str | None = None,
    line: int = 10,
    status: str = "OPEN",
) -> dict[str, Any]:
    global N
    N += 1
    ev: dict[str, Any] = {"kind": "probe", "probe_id": probe} if probe else {"kind": "code", "file_path": path}
    if not probe:
        ev |= {"line_start": line, "snippet_masked": snippet}
    return {
        "id": f"f{N}",
        "source": source,
        "category": category,
        "rule_id": rule,
        "title": f"{category} {path or probe}",
        "severity": "HIGH",
        "status": status,
        "evidence": [ev],
    }


def probe(pid: str, category: str, attempts: int, successes: int, **kw: Any) -> dict[str, Any]:
    return {"probe_id": pid, "category": category, "attempts": attempts, "successes": successes} | kw


def snap(
    findings: list[dict[str, Any]],
    *,
    mode: str = "full",
    probes: list[dict[str, Any]] | None = None,
    verification: str | None = "LIVE_VERIFIED",
    attacks: str = "COMPLETED",
    errors: list[dict[str, Any]] | None = None,
    tokens: int = 1000,
    aid: str = "00000000-0000-0000-0000-000000000001",
) -> dict[str, Any]:
    coverage = [{"target_id": "t", "attacks": attacks, "model_verification": verification}] if verification else []
    return {
        "audit_id": aid,
        "mode": mode,
        "created_at": "2026-10-23T00:00:00+00:00",
        "version": 1,
        "findings": findings,
        "report": {
            "targets": [{"type": "repo"}, {"type": "agent"}],
            "probes": probes or [],
            "agent_coverage": coverage,
            "errors": errors or [],
            "tokens": {"input": tokens},
        },
    }


BENIGN_OK = probe("UP-BENIGN-01", "BENIGN_CONTROL", 3, 3)


def by_title(r: dict[str, Any]) -> dict[str, str]:
    return {f["title"]: f["status"] for f in r["findings"]}


def test_same_issue_on_shifted_line_in_new_version_persists() -> None:
    base = snap([finding("SQL_INJECTION", "SEC-SQLI-001", "src/Repo.java", 'query("a" + x)', line=23)])
    cur = snap([finding("SQL_INJECTION", "SEC-SQLI-001", "src/Repo.java", 'query("a"   + x)', line=31)])
    r = compare(base, cur)
    assert [f["status"] for f in r["findings"]] == ["PERSISTING"]
    assert r["summary"]["PERSISTING"] == 1


def test_changed_snippet_same_rule_and_file_still_matches_loosely() -> None:
    base = snap([finding("HARDCODED_SECRET", "SEC-SECRET-002", "src/Pay.java", 'KEY = "abcd****"')])
    cur = snap([finding("HARDCODED_SECRET", "SEC-SECRET-002", "src/Pay.java", 'KEY2 = "zzzz****"')])
    assert [f["status"] for f in compare(base, cur)["findings"]] == ["PERSISTING"]


def test_fixed_code_issue_is_resolved_only_when_code_scan_ran() -> None:
    base = snap([finding("SECCONFIG_PERMIT_ALL", "SEC-SPRING-001", "src/Sec.java", "permitAll()")])
    assert by_title(compare(base, snap([])))["SECCONFIG_PERMIT_ALL src/Sec.java"] == "RESOLVED"
    agent_only = compare(base, snap([], mode="agent"))
    row = agent_only["findings"][0]
    assert row["status"] == "UNVERIFIED" and "코드 검사" in row["reason"]
    failed = compare(base, snap([], errors=[{"node": "code_static_scan", "code": "UPSTREAM_ERROR"}]))
    assert failed["findings"][0]["status"] == "UNVERIFIED"


def test_llm_found_issue_needs_llm_run_to_count_as_resolved() -> None:
    base = snap([finding("HARDCODED_SECRET", None, "src/Pay.java", "b64")])
    assert compare(base, snap([], tokens=0))["findings"][0]["status"] == "UNVERIFIED"
    assert compare(base, snap([], tokens=500))["findings"][0]["status"] == "RESOLVED"


def test_new_issue_and_false_positive_ignored() -> None:
    base = snap([finding("SQL_INJECTION", "SEC-SQLI-001", "a.java", "x", status="FALSE_POSITIVE")])
    cur = snap([finding("PATH_TRAVERSAL", "SEC-PATH-001", "b.java", "y")])
    r = compare(base, cur)
    assert [f["status"] for f in r["findings"]] == ["NEW"]


def test_same_attack_blocked_after_fix_with_benign_passing() -> None:
    base = snap(
        [finding("TOOL_MISUSE", "PROBE-UP-PATH-01", source="agent", probe="UP-PATH-01")],
        probes=[probe("UP-PATH-01", "TOOL_MISUSE", 3, 3), BENIGN_OK],
    )
    cur = snap([], probes=[probe("UP-PATH-01", "TOOL_MISUSE", 3, 0), BENIGN_OK])
    r = compare(base, cur)
    assert r["probes"][0]["status"] == "BLOCKED"
    assert r["findings"][0]["status"] == "RESOLVED"
    assert r["benign"] == {"baseline": "PASS", "current": "PASS"}


def test_block_is_unverified_when_benign_control_fails() -> None:
    base = snap([], probes=[probe("UP-PATH-01", "TOOL_MISUSE", 3, 3), BENIGN_OK])
    cur = snap([], probes=[probe("UP-PATH-01", "TOOL_MISUSE", 3, 0), probe("UP-BENIGN-01", "BENIGN_CONTROL", 3, 1)])
    r = compare(base, cur)
    assert r["probes"][0]["status"] == "UNVERIFIED"
    assert "정상 대조군" in r["probes"][0]["reason"]
    assert any("정상 대조군" in n for n in r["notes"])


def test_not_run_errored_or_uncertain_attack_is_never_resolved() -> None:
    f = finding("TOOL_MISUSE", "PROBE-P03", source="agent", probe="P03")
    base = snap([f], probes=[probe("P03", "TOOL_MISUSE", 3, 2), BENIGN_OK])
    for cur in (
        snap([], probes=[BENIGN_OK]),  # 같은 공격이 이번에 실행되지 않음
        snap([], probes=[probe("P03", "TOOL_MISUSE", 0, 0, error="UPSTREAM_TIMEOUT"), BENIGN_OK]),
        snap([], probes=[probe("P03", "TOOL_MISUSE", 3, 0, uncertain=2), BENIGN_OK]),
        snap([], probes=[probe("P03", "TOOL_MISUSE", 3, 0), BENIGN_OK], attacks="NOT_RUN"),
    ):
        r = compare(base, cur)
        assert r["probes"][0]["status"] == "UNVERIFIED", cur
        assert r["findings"][0]["status"] == "UNVERIFIED"


def test_mock_vs_live_runs_are_not_comparable() -> None:
    base = snap([], probes=[probe("UP-RAG-01", "INDIRECT_INJECTION", 1, 1), BENIGN_OK], verification="LIVE_VERIFIED")
    cur = snap([], probes=[probe("UP-RAG-01", "INDIRECT_INJECTION", 1, 0), BENIGN_OK], verification="MOCK")
    r = compare(base, cur)
    assert r["probes"][0]["status"] == "UNVERIFIED"
    assert any("실행 모드가 다릅니다" in n for n in r["notes"])


def test_still_successful_attack_persists_and_new_hole_is_new() -> None:
    base = snap([], probes=[probe("P03", "TOOL_MISUSE", 3, 3), probe("P05", "INDIRECT_INJECTION", 3, 0), BENIGN_OK])
    cur = snap([], probes=[probe("P03", "TOOL_MISUSE", 3, 1), probe("P05", "INDIRECT_INJECTION", 3, 2), BENIGN_OK])
    status = {p["probe_id"]: p["status"] for p in compare(base, cur)["probes"]}
    assert status == {"P03": "PERSISTING", "P05": "NEW"}


def test_adaptive_variants_roll_up_to_base_probe() -> None:
    base = snap([], probes=[probe("P03", "TOOL_MISUSE", 3, 0), probe("P03-A01", "TOOL_MISUSE", 3, 2, base_probe_id="P03")])
    cur = snap([], probes=[probe("P03", "TOOL_MISUSE", 3, 0), BENIGN_OK])
    rows = compare(base, cur)["probes"]
    assert [(p["probe_id"], p["baseline"], p["status"]) for p in rows] == [
        ("P03", {"attempts": 6, "successes": 2}, "BLOCKED")
    ]


def test_markdown_lists_both_sections() -> None:
    base = snap(
        [finding("SQL_INJECTION", "SEC-SQLI-001", "a.java", "x")],
        probes=[probe("P03", "TOOL_MISUSE", 3, 3), BENIGN_OK],
    )
    md = render_md(compare(base, snap([], probes=[probe("P03", "TOOL_MISUSE", 3, 0), BENIGN_OK])))
    assert "동일 공격 재검증" in md and "재검증에서 차단" in md and "재검증에서 해소" in md
