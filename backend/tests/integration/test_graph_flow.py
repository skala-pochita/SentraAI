"""Phase 5·6 그래프 경로 (LLM 없음, in-process MCP, 실제 Postgres, fakeredis, InMemorySaver).

- 감사가 intake → code/agent 서브그래프 → correlate → human_approval(interrupt) 까지 진행
- 승인 → fix_generate → apply_fix → wait_merge(interrupt) → 병합 → re_verify → report
- 반려 경로: risk_accept 후 비-CRITICAL 만 수정
- 승인 없는 apply_fix → FORBIDDEN
- '워커 재시작': 새 그래프 인스턴스 + 같은 체크포인터로 재개
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from sqlalchemy import select

from api.seed import seed_users
from core.db.models import Approval, Audit, AuditTarget, Finding, FixProposalRow, Report, Target
from core.db.session import session_scope
from core.graph.build import build_graph
from core.graph.deps import Deps
from core.mcp_client import CallCtx, inprocess_gateway
from core.settings import REPO_ROOT
from mcp_servers.business.demo_reset import reset
from mcp_servers.business.tools import execute
from tests.fakes import GullibleAgent


@pytest.fixture
async def env(db_ready: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict[str, Any]]:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("PROBE_ATTEMPTS", "1")
    monkeypatch.setenv("PROBE_ALLOWLIST", "http://target-agent:8201")
    from core.settings import get_settings

    get_settings.cache_clear()
    reset(REPO_ROOT / "targets", tmp_path, "vuln")
    agent_sim = GullibleAgent(tmp_path / "agent-config")

    async def fake_chat(client: Any, url: str, sid: str, message: str, kb: Any) -> dict[str, Any]:
        return agent_sim.chat(message, kb)

    monkeypatch.setattr(execute, "_chat", fake_chat)
    async with session_scope() as db:
        users = await seed_users(db)
        repo = Target(
            type="repo",
            name="vuln-spring-app",
            ref=str(tmp_path / "vuln-spring-app"),
            owner_user_id=users["DEVELOPER"].id,
        )
        db.add(repo)
        await db.flush()
        agent = Target(
            type="agent",
            name="cs-helper",
            ref="http://target-agent:8201",
            linked_target_id=repo.id,
            config={"config_dir": str(tmp_path / "agent-config")},
        )
        db.add(agent)
        await db.flush()
        repo.linked_target_id = agent.id
        admin = users["ADMIN"]
        audit = Audit(requested_by=admin.id, mode="full", status="RUNNING", thread_id="t")
        db.add(audit)
        await db.flush()
        db.add_all(
            [AuditTarget(audit_id=audit.id, target_id=repo.id), AuditTarget(audit_id=audit.id, target_id=agent.id)]
        )
        ids = {"admin": str(admin.id), "repo": str(repo.id), "agent": str(agent.id), "audit": str(audit.id)}
    yield {**ids, "workspace": tmp_path}
    get_settings.cache_clear()


def initial(env: dict[str, Any]) -> dict[str, Any]:
    return {
        "audit_id": env["audit"],
        "thread_id": f"audit:{env['audit']}",
        "requested_by": env["admin"],
        "actor_role": "ADMIN",
        "mode": "full",
        "eval_config": None,
        "targets": [{"target_id": env["repo"]}, {"target_id": env["agent"]}],
    }


def cfg(env: dict[str, Any]) -> dict[str, Any]:
    return {
        "configurable": {"thread_id": f"audit:{env['audit']}", "deps": Deps(gateway=inprocess_gateway())},
        "recursion_limit": 100,
    }


async def decide(approval_id: str, admin: str, decision: str) -> dict[str, Any]:
    async with session_scope() as db:
        a = await db.get(Approval, uuid.UUID(approval_id))
        assert a is not None
        a.status = "APPROVED" if decision == "APPROVE" else "REJECTED"
        a.decided_by, a.decided_at = uuid.UUID(admin), datetime.now(UTC)
    return {"approval_id": approval_id, "decision": decision, "decided_by": admin, "comment": "ok"}


async def merge_all(env: dict[str, Any]) -> None:
    gw = inprocess_gateway()
    async with session_scope() as db:
        props = list(
            (
                await db.execute(
                    select(FixProposalRow).where(
                        FixProposalRow.audit_id == uuid.UUID(env["audit"]), FixProposalRow.branch.is_not(None)
                    )
                )
            ).scalars()
        )
    for p in props:
        await gw.call("merge_fix", {"proposal_id": str(p.id)}, CallCtx("api", "ADMIN", env["admin"], env["audit"]))


async def test_full_flow_approve_merge_reverify(env: dict[str, Any]) -> None:
    saver = InMemorySaver()
    graph = build_graph(saver)
    await graph.ainvoke(initial(env), cfg(env))
    snap = await graph.aget_state(cfg(env))
    assert snap.next == ("human_approval",)
    st = snap.values
    cats = {f["category"] for f in st["correlated"]}
    assert {"SECCONFIG_PERMIT_ALL", "TOOL_NO_APPROVAL", "AUDITOR_INJECTION_ATTEMPT", "COMBINED_RISK"} <= cats
    rules = {f["rule_id"] for f in st["correlated"] if f["source"] == "correlation"}
    assert {"CORR-001", "CORR-003"} <= rules, rules
    vulnerable = {r["probe_id"] for r in st["probe_results"] if r["successes"]}
    assert {"P01", "P03", "P05"} <= vulnerable
    benign = next(r for r in st["probe_results"] if r["probe_id"] == "P12")
    assert benign["successes"] == 1  # 정상 응답 (오탐 아님)

    # 승인 없는 apply_fix → FORBIDDEN (human_approval 전이라 수정안이 없으므로 직접 만든다)
    approval_id = snap.tasks[0].interrupts[0].value["approval_id"]

    # '워커 재시작': 새 그래프 인스턴스, 같은 체크포인터
    graph2 = build_graph(saver)
    resume = await decide(approval_id, env["admin"], "APPROVE")
    await graph2.ainvoke(Command(resume=resume), cfg(env))
    snap = await graph2.aget_state(cfg(env))
    assert snap.next == ("wait_merge",)
    proposals = snap.values["fix_proposals"]
    assert {p["kind"] for p in proposals} >= {"code_patch", "manifest_patch"}
    assert all(p["branch"] for p in proposals)

    await merge_all(env)
    await graph2.ainvoke(Command(resume={"merged": True}), cfg(env))
    snap = await graph2.aget_state(cfg(env))
    assert snap.next == ()
    v = snap.values["verify_result"]
    assert v["resolved"] and snap.values["report_id"]

    async with session_scope() as db:
        rows = list((await db.execute(select(Finding).where(Finding.audit_id == uuid.UUID(env["audit"])))).scalars())
        by_cat = {f.category: f.status for f in rows}
        assert by_cat["SECCONFIG_PERMIT_ALL"] == "RESOLVED"
        assert by_cat["TOOL_NO_APPROVAL"] == "RESOLVED"
        report = (await db.execute(select(Report).where(Report.audit_id == uuid.UUID(env["audit"])))).scalar_one()
        assert "보안 감사 보고서" in report.report_md
        assert "AKIAFAKE0EXAMPLE0000" not in report.report_md
    # hardened 반영 확인: 병합된 manifest 로 P03 재실행 시 차단
    assert v["probe_reruns"].get("P03") == 0
    assert (env["workspace"] / "agent-config/manifest.yaml").read_text().count("requires_approval: true") >= 3


async def test_reject_path_accepts_risk_and_fixes_noncritical(env: dict[str, Any]) -> None:
    graph = build_graph(InMemorySaver())
    await graph.ainvoke(initial(env), cfg(env))
    snap = await graph.aget_state(cfg(env))
    approval_id = snap.tasks[0].interrupts[0].value["approval_id"]
    await graph.ainvoke(Command(resume=await decide(approval_id, env["admin"], "REJECT")), cfg(env))
    snap = await graph.aget_state(cfg(env))
    assert snap.values["accepted_risks"]
    crit = {f["finding_id"] for f in snap.values["correlated"] if f["severity"] == "CRITICAL"}
    for p in snap.values["fix_proposals"]:
        assert not (set(p["finding_ids"]) & crit)


async def test_apply_fix_without_approval_forbidden(env: dict[str, Any]) -> None:
    graph = build_graph(InMemorySaver())
    await graph.ainvoke(initial(env), cfg(env))
    snap = await graph.aget_state(cfg(env))
    crit = next(f for f in snap.values["correlated"] if f["category"] == "SECCONFIG_PERMIT_ALL")
    gw = inprocess_gateway()
    ctx = CallCtx("fix_generate", "ADMIN", env["admin"], env["audit"])
    res = await gw.call(
        "create_fix_proposal",
        {
            "audit_id": env["audit"],
            "finding_ids": [crit["finding_id"]],
            "kind": "code_patch",
            "target_id": env["repo"],
            "edits": [
                {
                    "op": "line_regex",
                    "path": crit["evidence"][0]["file_path"],
                    "line": crit["evidence"][0]["line_start"],
                    "pattern": r"permitAll\(\)",
                    "replacement": "authenticated()",
                }
            ],
        },
        ctx,
    )
    assert res["patch_check_passed"] and res["precheck_remaining"] == []
    from core.errors import AppError, ErrorCode

    ctx2 = CallCtx("apply_fix", "ADMIN", env["admin"], env["audit"])
    with pytest.raises(AppError) as ei:
        await gw.call("apply_fix", {"proposal_id": res["proposal_id"], "idempotency_key": "k1"}, ctx2)
    assert ei.value.code == ErrorCode.FORBIDDEN
    # DEVELOPER 역할은 H3 게이트에서 변경 도구 자체가 거부된다
    with pytest.raises(AppError) as ei:
        await gw.call(
            "apply_fix",
            {"proposal_id": res["proposal_id"], "idempotency_key": "k1"},
            CallCtx("apply_fix", "DEVELOPER", env["admin"], env["audit"]),
        )
    assert ei.value.code == ErrorCode.FORBIDDEN
