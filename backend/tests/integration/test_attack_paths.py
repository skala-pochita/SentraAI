"""Real PostgreSQL + in-process MCP + API: path persistence, references and replay."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from api.security import create_token
from core.db.models import Audit, ProbeRun, RiskPath
from core.db.session import session_scope
from core.errors import AppError
from core.graph.build import build_graph
from core.mcp_client import CallCtx, inprocess_gateway
from tests.integration.test_graph_flow import cfg, env, initial  # noqa: F401


async def test_paths_have_drilldown_and_replay_safety(env: dict[str, Any], client: httpx.AsyncClient) -> None:  # noqa: F811
    graph = build_graph(InMemorySaver())
    await graph.ainvoke(initial(env), cfg(env))
    snapshot = (await graph.aget_state(cfg(env))).values
    paths = snapshot["risk_paths"]
    assert {"CORR-001", "CORR-002", "CORR-003", "CORR-004"} <= {p["rule_id"] for p in paths}
    assert all(p["payload"]["schema_version"] == 1 for p in paths)
    token, _, _ = create_token(env["admin"], "ADMIN", "access")
    headers = {"Authorization": f"Bearer {token}"}
    aid = env["audit"]
    assert (await client.get(f"/api/v1/audits/{aid}/risk-paths", headers=headers)).status_code == 409
    async with session_scope() as db:
        audit = await db.get(Audit, uuid.UUID(aid))
        audit.status = "COMPLETED"
    rows = (await client.get(f"/api/v1/audits/{aid}/risk-paths", headers=headers)).json()
    dynamic = next(p for p in rows if p["rule_id"] == "CORR-001")
    evidence = await client.get(f"/api/v1/audits/{aid}/risk-paths/{dynamic['id']}/evidence", headers=headers)
    assert evidence.status_code == 200, evidence.text
    assert any(
        reason["kind"] == "implementation_candidate" and reason["ref"]["file_path"] == "policy.py"
        for reason in dynamic["payload"]["code_reasons"]
    )
    assert dynamic["payload"]["code_reasons"]
    assert "CRITICAL" in dynamic["payload"]["severity_reason"]
    assert any("requires_approval: false" in reason["snippet_masked"] for reason in dynamic["payload"]["code_reasons"])
    assert evidence.json()["probe_runs"]
    assert all(r["verdict"] == "SUCCESS" for r in evidence.json()["probe_runs"])
    assert "조건부 추정" in (await client.get(f"/api/v1/audits/{aid}/report.md", headers=headers)).text

    gw = inprocess_gateway()
    ctx = CallCtx("correlate", "ADMIN", env["admin"], aid)
    f = next(f for f in snapshot["correlated"] if f["rule_id"] == "CORR-001")
    await gw.call("create_finding", {"audit_id": aid, "finding": f}, ctx)
    again = (await client.get(f"/api/v1/audits/{aid}/risk-paths", headers=headers)).json()
    assert len(rows) == len(again)
    broken = dict(f)
    broken["attack_path"] = dict(f["attack_path"])
    broken["attack_path"]["steps"] = [dict(s) for s in f["attack_path"]["steps"]]
    broken["attack_path"]["steps"][1]["evidence_refs"] = [
        {"target_id": env["agent"], "probe_run_id": str(uuid.uuid4()), "tool_call_index": 0}
    ]
    with pytest.raises(AppError):
        await gw.call("create_finding", {"audit_id": aid, "finding": broken}, ctx)

    # Displayed code must be the exact snapshot saved on the referenced Finding.
    from copy import deepcopy

    forged = deepcopy(f)
    forged["attack_path"]["code_reasons"][0]["snippet_masked"] = "invented code"
    with pytest.raises(AppError):
        await gw.call("create_finding", {"audit_id": aid, "finding": forged}, ctx)

    forged_kind = deepcopy(f)
    forged_kind["attack_path"]["code_reasons"][0]["kind"] = "code_finding"
    with pytest.raises(AppError):
        await gw.call("create_finding", {"audit_id": aid, "finding": forged_kind}, ctx)

    # A real run from another audit must be rejected even when its target matches.
    async with session_scope() as db:
        other_audit = Audit(requested_by=uuid.UUID(env["admin"]), mode="full", status="RUNNING", thread_id="other")
        db.add(other_audit)
        await db.flush()
        foreign = ProbeRun(
            audit_id=other_audit.id,
            target_id=uuid.UUID(env["agent"]),
            probe_id="P",
            category="TOOL_MISUSE",
            channel="direct",
            attempt=1,
            request={},
            reply_masked="",
            tool_calls=[{"name": "delete_customer", "executed": True}],
            success=True,
            verdict="SUCCESS",
            judged_by="deterministic",
        )
        db.add(foreign)
        await db.flush()
        foreign_id = str(foreign.id)
    broken["attack_path"]["steps"][1]["evidence_refs"][0]["probe_run_id"] = foreign_id
    with pytest.raises(AppError):
        await gw.call("create_finding", {"audit_id": aid, "finding": broken}, ctx)

    # Historical records still work, without invented steps.
    async with session_scope() as db:
        legacy = RiskPath(
            audit_id=uuid.UUID(aid),
            rule_id="LEGACY",
            severity="HIGH",
            title="old",
            finding_ids=[],
            explanation="old format",
        )
        db.add(legacy)
        await db.flush()
        legacy_id = str(legacy.id)
    legacy_result = await client.get(f"/api/v1/audits/{aid}/risk-paths/{legacy_id}/evidence", headers=headers)
    assert legacy_result.json() == {"findings": [], "probe_runs": []}


async def test_finalize_verdict_is_scoped_and_idempotent(env: dict[str, Any]) -> None:  # noqa: F811
    aid = uuid.UUID(env["audit"])
    async with session_scope() as db:
        row = ProbeRun(
            audit_id=aid,
            target_id=uuid.UUID(env["agent"]),
            probe_id="TEST",
            category="TOOL_MISUSE",
            channel="direct",
            attempt=1,
            request={},
            reply_masked="",
            tool_calls=[],
            success=False,
            verdict="UNDETERMINED",
            judged_by="deterministic",
        )
        db.add(row)
        await db.flush()
        rid = str(row.id)
    gw = inprocess_gateway()
    args = {"probe_run_id": rid, "verdict": "SUCCESS"}
    ctx = CallCtx("probe_judge", "ADMIN", env["admin"], str(aid))
    assert (await gw.call("finalize_probe_verdict", args, ctx))["updated"]
    assert not (await gw.call("finalize_probe_verdict", args, ctx))["updated"]
    with pytest.raises(AppError):
        await gw.call("finalize_probe_verdict", args, CallCtx("probe_judge", "ADMIN", env["admin"], str(uuid.uuid4())))
    with pytest.raises(AppError):
        await gw.call("finalize_probe_verdict", args, CallCtx("code_investigate", "ADMIN", env["admin"], str(aid)))
    async with session_scope() as db:
        row = (await db.execute(select(ProbeRun).where(ProbeRun.id == uuid.UUID(rid)))).scalar_one()
        assert row.verdict == "SUCCESS" and row.success and row.judged_by == "llm"
