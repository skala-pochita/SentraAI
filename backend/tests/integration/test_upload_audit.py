"""Upload → scan → report, result visibility and ownership regression coverage."""

from __future__ import annotations

import io
import uuid
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from api.seed import seed_users
from core.db.models import Audit, AuditTarget, Finding, Target
from core.db.session import session_scope
from core.graph.build import build_graph
from core.graph.deps import Deps
from core.mcp_client import inprocess_gateway
from core.settings import get_settings
from tests.integration.test_auth import P, h, tokens


def project_zip() -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        z.writestr(
            "project/src/main/resources/application.yml",
            "spring:\n  datasource:\n    password: UploadFakePassword123!\n",
        )
        z.writestr(
            "project/src/main/java/demo/Config.java", 'class Config { String key = "sk_live_FAKEUPLOAD1234567890"; }\n'
        )
    return data.getvalue()


@pytest.fixture
async def users(client: httpx.AsyncClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()
    async with session_scope() as db:
        await seed_users(db)
    result = await tokens(client)
    yield result
    get_settings.cache_clear()


async def test_upload_scan_complete_visibility(client: httpx.AsyncClient, users: dict[str, Any]) -> None:
    headers = h(users["DEVELOPER"])
    upload = await client.post(
        f"{P}/targets/upload", headers=headers, files={"file": ("source.zip", project_zip(), "application/zip")}
    )
    assert upload.status_code == 201, upload.text
    target = upload.json()
    root = Path(target["ref"])
    before = {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    async with session_scope() as db:
        stored = await db.get(Target, uuid.UUID(target["id"]))
        audit = Audit(requested_by=stored.owner_user_id, mode="code", status="RUNNING", thread_id="upload-test")
        db.add(audit)
        await db.flush()
        audit_id = str(audit.id)
        db.add(AuditTarget(audit_id=audit.id, target_id=stored.id))
    graph = build_graph(InMemorySaver())
    deps = Deps(gateway=inprocess_gateway(), emit_events=False)
    await graph.ainvoke(
        {
            "audit_id": audit_id,
            "mode": "code",
            "actor_role": "DEVELOPER",
            "requested_by": str(stored.owner_user_id),
            "targets": [{"target_id": target["id"]}],
        },
        {"configurable": {"thread_id": audit_id, "deps": deps}},
    )
    async with session_scope() as db:
        rows = (await db.execute(select(Finding).where(Finding.audit_id == uuid.UUID(audit_id)))).scalars().all()
        assert rows
        finding_id = str(rows[0].id)
    # Even persisted findings/report remain private until final completion.
    for suffix in ["findings", "risk-paths", "probes", "report", "report.md"]:
        response = await client.get(f"{P}/audits/{audit_id}/{suffix}", headers=headers)
        assert response.status_code == 409, response.text
    assert (await client.get(f"{P}/findings/{finding_id}", headers=headers)).status_code == 409
    async with session_scope() as db:
        audit = await db.get(Audit, uuid.UUID(audit_id))
        audit.status = "COMPLETED"
    assert (await client.get(f"{P}/audits/{audit_id}/report.md", headers=headers)).status_code == 200
    assert (await client.get(f"{P}/audits/{audit_id}/findings", headers=headers)).json()
    assert before == {str(p): p.read_bytes() for p in root.rglob("*") if p.is_file()}


async def test_upload_permissions_and_invalid_zip(client: httpx.AsyncClient, users: dict[str, Any]) -> None:
    for role, expected in [("AUDITOR", 403), ("DEVELOPER", 201), ("ADMIN", 201)]:
        response = await client.post(
            f"{P}/targets/upload", headers=h(users[role]), files={"file": ("source.zip", project_zip())}
        )
        assert response.status_code == expected, response.text
    response = await client.post(
        f"{P}/targets/upload", headers=h(users["DEVELOPER"]), files={"file": ("bad.zip", b"bad")}
    )
    assert response.status_code == 422
    assert (await client.post(f"{P}/targets/upload", files={"file": ("source.zip", project_zip())})).status_code == 401


def test_public_routes_do_not_expose_retired_features() -> None:
    from api.main import app

    paths = set(app.openapi()["paths"])
    assert not any("fixes" in p or "approvals" in p or "github" in p for p in paths)


async def test_upload_full_agent_audit_uses_project_probes_and_cleans_sandbox(
    client: httpx.AsyncClient,
    users: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import api.routers.audits as audit_api
    import core.sandbox_client as sandbox_client
    from core.project_probes import canary

    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        z.writestr(
            "project/agent/app/tools.py", 'TOOL_SCHEMAS = [{"type":"function","function":{"name":"read_file"}}]\n'
        )
        z.writestr("project/agent/app/main.py", 'routes = ["/sessions", "/chat", "session_id"]\n')
        z.writestr("project/agent/Dockerfile", "FROM python:3.12-slim\n")
        z.writestr(
            "project/compose.yaml",
            'services:\n  agent:\n    build: {context: ., dockerfile: agent/Dockerfile}\n    ports: ["8000"]\n',
        )
        z.writestr("project/backend/src/main/java/lab/target/Controller.java", "class Controller {}\n")
    uploaded = await client.post(
        f"{P}/targets/upload", headers=h(users["DEVELOPER"]), files={"file": ("agent.zip", data.getvalue())}
    )
    assert uploaded.status_code == 201, uploaded.text
    project = uploaded.json()
    assert project["linked_target_id"] and project["config"]["runtime"]["supported"]
    lifecycle = []

    async def fake_publish(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(audit_api, "publish", fake_publish)
    response = await client.post(
        f"{P}/audits", headers=h(users["DEVELOPER"]), json={"target_ids": [project["id"]], "mode": "full"}
    )
    assert response.status_code == 202, response.text
    aid = response.json()["audit_id"]

    async def fake_runtime(path: str, payload: Any = None, timeout: Any = None) -> dict[str, Any]:
        lifecycle.append(path)
        if path == "/start":
            return {"status": "READY", "mode": "mock"}
        if path == "/chat":
            return {
                "reply": "hello" if payload["message"] == "onboarding" else canary(aid),
                "tool_calls": [{"name": "read_file", "args": {}, "executed": True}],
            }
        return {"status": "STOPPED"}

    monkeypatch.setattr(sandbox_client, "sandbox_call", fake_runtime)
    async with session_scope() as db:
        audit = await db.get(Audit, uuid.UUID(aid))
        audit.status = "RUNNING"
        rows = (await db.execute(select(AuditTarget).where(AuditTarget.audit_id == audit.id))).scalars().all()
        assert len(rows) == 2
        uid = str(audit.requested_by)
    deps = Deps(gateway=inprocess_gateway(), emit_events=False)
    graph = build_graph(InMemorySaver())
    config = {"configurable": {"thread_id": aid, "deps": deps}}
    await graph.ainvoke(
        {
            "audit_id": aid,
            "mode": "full",
            "actor_role": "DEVELOPER",
            "requested_by": uid,
            "targets": [{"target_id": str(t.target_id)} for t in rows],
        },
        config,
    )
    values = (await graph.aget_state(config)).values
    assert values["report_id"]
    assert {p["probe_id"] for p in values["probe_results"]} == {"UP-PATH-01", "UP-RAG-01", "UP-BENIGN-01"}
    assert "/start" in lifecycle and f"/stop/{aid}" in lifecycle
    assert {f["source"] for f in values["correlated"]} >= {"agent"}
    assert values["agent_coverage"][0]["runtime"]["mode"] == "mock"


@pytest.mark.parametrize("name", [None, "", "   ", "  상담 AI 2차 점검  "])
async def test_audit_name_persists_in_history(
    client: httpx.AsyncClient, users: dict[str, Any], monkeypatch: pytest.MonkeyPatch, name: str | None
) -> None:
    import api.routers.audits as audit_api

    async def no_publish(*args: Any, **kwargs: Any) -> None:
        pass

    monkeypatch.setattr(audit_api, "publish", no_publish)
    headers = h(users["DEVELOPER"])
    upload = await client.post(
        f"{P}/targets/upload", headers=headers, files={"file": ("source.zip", project_zip(), "application/zip")}
    )
    assert upload.status_code == 201, upload.text
    target = upload.json()
    body = {"target_ids": [target["id"]], "mode": "code", **({"name": name} if name is not None else {})}
    created = await client.post(f"{P}/audits", headers=headers, json=body)
    assert created.status_code == 202, created.text
    audit_id = created.json()["audit_id"]
    expected = (name or "").strip() or target["name"]
    detail = await client.get(f"{P}/audits/{audit_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["name"] == expected
    history = await client.get(f"{P}/audits", headers=headers)
    assert history.status_code == 200, history.text
    assert next(a for a in history.json()["items"] if a["id"] == audit_id)["name"] == expected
