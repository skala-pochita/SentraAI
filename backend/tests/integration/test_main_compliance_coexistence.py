"""Current main features and compliance share the real DB/API/report pipeline.

The small regulation fixture is synthetic; this tests integration, not relevance.
"""

from __future__ import annotations

import uuid
from shutil import copyfile
from typing import Any

import httpx
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from api.security import create_token
from core.compliance import render_compliance, verify_report
from core.compliance_cli import atomic_yaml
from core.db.models import Audit, Report, User
from core.db.session import session_scope
from core.graph.build import build_graph
from core.settings import REPO_ROOT, get_settings, load_config
from tests.integration.test_graph_flow import cfg, env, initial  # noqa: F401
from tests.unit.test_compliance import link


async def test_attack_paths_and_compliance_share_saved_report(
    env: dict[str, Any], originals: Any, client: httpx.AsyncClient  # noqa: F811
) -> None:
    _, config, _ = originals
    for source in (REPO_ROOT / "config").glob("*.yaml"):
        if not (config.parent / source.name).exists():
            copyfile(source, config.parent / source.name)
    atomic_yaml(config / "links/default.yaml", [link().model_dump()])
    get_settings.cache_clear()
    load_config.cache_clear()
    graph = build_graph(InMemorySaver())
    state = initial(env)
    state["compliance_profile"] = {"sector": "finance", "high_impact_ai": "UNDETERMINED"}
    await graph.ainvoke(state, cfg(env))
    snapshot = (await graph.aget_state(cfg(env))).values
    assert snapshot["report_id"]
    token, _, _ = create_token(env["admin"], "ADMIN", "access")
    headers = {"Authorization": f"Bearer {token}"}
    prefix = f"/api/v1/audits/{env['audit']}"
    assert (await client.get(prefix + "/compliance", headers=headers)).status_code == 409
    async with session_scope() as db:
        stored = (await db.execute(select(Report).where(Report.audit_id == uuid.UUID(env["audit"])))).scalar_one()
        saved, markdown = stored.report_json, stored.report_md
        (await db.get(Audit, uuid.UUID(env["audit"]))).status = "COMPLETED"
        developer = (await db.execute(select(User).where(User.role == "DEVELOPER"))).scalar_one()
        developer_id = str(developer.id)
    assert verify_report(saved)
    assert saved["compliance"]["recommendations"]
    assert saved["compliance_profile"]["sector"] == "finance"
    assert all(r["status"] == "NOT_ASSESSED" for r in saved["compliance"]["recommendations"])
    assert "## 결합 위험" in markdown and "규제·가이드라인 검토 권고" in markdown
    paths = (await client.get(prefix + "/risk-paths", headers=headers)).json()
    attack = next(p for p in paths if p["rule_id"] == "CORR-001")
    assert attack["payload"]["steps"] and attack["payload"]["code_reasons"]
    evidence = await client.get(prefix + f"/risk-paths/{attack['id']}/evidence", headers=headers)
    assert evidence.status_code == 200 and evidence.json()["probe_runs"]
    export = await client.get(prefix + "/compliance", headers=headers)
    assert export.status_code == 200 and export.json()["report_snapshot"] == saved
    assert (await client.get(prefix + "/compliance.md", headers=headers)).text == render_compliance(saved)
    assert (await client.get(prefix + "/report.md", headers=headers)).text == markdown
    mapped = next(f for f in saved["findings"] if f["compliance_refs"])
    finding = await client.get(f"/api/v1/findings/{mapped['finding_id']}", headers=headers)
    assert finding.status_code == 200 and finding.json()["compliance_refs"] == mapped["compliance_refs"]
    dev_token, _, _ = create_token(developer_id, "DEVELOPER", "access")
    developer_headers = {"Authorization": f"Bearer {dev_token}"}
    assert (await client.get(prefix + "/compliance", headers=developer_headers)).status_code == 403
    developer_report = await client.get(prefix + "/report", headers=developer_headers)
    assert developer_report.status_code == 200
    assert "compliance" not in developer_report.json() and "compliance_profile" not in developer_report.json()
    assert all("compliance_refs" not in f for f in developer_report.json()["findings"])
    developer_markdown = await client.get(prefix + "/report.md", headers=developer_headers)
    assert developer_markdown.status_code == 200
    assert "규제·가이드라인 검토 권고" not in developer_markdown.text
    assert "## 결합 위험" in developer_markdown.text and "## Finding 목록" in developer_markdown.text


async def test_baseline_and_compliance_profile_survive_audit_creation(
    env: dict[str, Any], client: httpx.AsyncClient, monkeypatch: Any  # noqa: F811
) -> None:
    from unittest.mock import AsyncMock

    import api.routers.audits as audits
    from workers.audit import AuditRunner

    async with session_scope() as db:
        (await db.get(Audit, uuid.UUID(env["audit"]))).status = "COMPLETED"
    monkeypatch.setattr(audits, "publish", AsyncMock())
    token, _, _ = create_token(env["admin"], "ADMIN", "access")
    profile = {"sector": "finance", "high_impact_ai": "YES"}
    response = await client.post(
        "/api/v1/audits",
        headers={"Authorization": f"Bearer {token}"},
        json={"target_ids": [env["repo"]], "mode": "code", "baseline_audit_id": env["audit"], "compliance_profile": profile},
    )
    assert response.status_code == 202, response.text
    async with session_scope() as db:
        created = await db.get(Audit, uuid.UUID(response.json()["audit_id"]))
        assert str(created.baseline_audit_id) == env["audit"]
        assert created.summary["compliance_profile"] == profile
    worker = AuditRunner(InMemorySaver())
    worker_state = await worker._initial(created)
    assert worker_state["compliance_profile"] == profile
    assert worker_state["mode"] == "code" and worker_state["targets"]
