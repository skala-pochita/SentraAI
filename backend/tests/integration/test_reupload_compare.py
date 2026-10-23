"""수정본 재업로드: 버전 연결 → 실행 설정 승계 → 기준 감사 지정 → 동일 공격 재실행 → 비교."""

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

from core.db.models import Audit, AuditTarget, ExecutionSettings
from core.db.session import session_scope
from core.graph.build import build_graph
from core.graph.deps import Deps
from core.mcp_client import inprocess_gateway
from core.secret_store import SecretStore
from core.settings import get_settings
from tests.integration.test_auth import P, h
from tests.integration.test_upload_audit import project_zip

pytest_plugins = ["tests.integration.test_upload_audit"]

LEAK = 'class Controller { String key = "sk_live_FAKEREUPLOAD1234567890"; }\n'


def agent_zip(controller: str) -> bytes:
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
        z.writestr("project/backend/src/main/java/lab/target/Controller.java", controller)
    return data.getvalue()


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import api.routers.audits as audit_api

    async def fake_publish(*args: Any, **kwargs: Any) -> None:
        return None

    monkeypatch.setattr(audit_api, "publish", fake_publish)
    monkeypatch.setenv("VAULT_DATA_DIR", str(tmp_path / "ciphertext"))
    monkeypatch.setenv("VAULT_KEY_FILE", str(tmp_path / "keys/master.key"))
    get_settings.cache_clear()


async def upload(client: httpx.AsyncClient, token: str, body: bytes, previous: str | None = None) -> dict[str, Any]:
    response = await client.post(
        f"{P}/targets/upload",
        headers=h(token),
        files={"file": ("agent.zip", body)},
        data={"previous_target_id": previous} if previous else None,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def run_audit(
    client: httpx.AsyncClient,
    token: str,
    target_id: str,
    monkeypatch: pytest.MonkeyPatch,
    *,
    vulnerable: bool,
    baseline: str | None = None,
) -> str:
    """API 로 감사를 만들고 그래프를 in-process 로 끝까지 돌린 뒤 완료 처리한다 (sandbox 는 대역)."""
    import core.sandbox_client as sandbox_client
    from core.project_probes import canary

    body: dict[str, Any] = {"target_ids": [target_id], "mode": "full"}
    if baseline:
        body["baseline_audit_id"] = baseline
    response = await client.post(f"{P}/audits", headers=h(token), json=body)
    assert response.status_code == 202, response.text
    aid = response.json()["audit_id"]

    async def fake_runtime(path: str, payload: Any = None, timeout: Any = None) -> dict[str, Any]:
        if path == "/start":
            return {"status": "READY", "mode": "mock"}
        if path == "/chat":
            if payload["message"] == "onboarding":
                return {"reply": "hello", "tool_calls": []}
            if vulnerable:
                return {"reply": canary(aid), "tool_calls": [{"name": "read_file", "args": {}, "executed": True}]}
            return {"reply": "접근할 수 없는 경로입니다.", "tool_calls": []}
        return {"status": "STOPPED"}

    monkeypatch.setattr(sandbox_client, "sandbox_call", fake_runtime)
    async with session_scope() as db:
        audit = await db.get(Audit, uuid.UUID(aid))
        audit.status = "RUNNING"
        rows = (await db.execute(select(AuditTarget).where(AuditTarget.audit_id == audit.id))).scalars().all()
        uid = str(audit.requested_by)
    graph = build_graph(InMemorySaver())
    await graph.ainvoke(
        {
            "audit_id": aid,
            "mode": "full",
            "actor_role": "DEVELOPER",
            "requested_by": uid,
            "targets": [{"target_id": str(t.target_id)} for t in rows],
        },
        {"configurable": {"thread_id": aid, "deps": Deps(gateway=inprocess_gateway(), emit_events=False)}},
    )
    async with session_scope() as db:
        (await db.get(Audit, uuid.UUID(aid))).status = "COMPLETED"
    return aid


async def test_fixed_reupload_reruns_same_attacks_and_compares(
    client: httpx.AsyncClient, users: dict[str, Any], env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    dev = users["DEVELOPER"]
    v1 = await upload(client, dev, agent_zip(LEAK))
    assert v1["config"]["version"] == 1 and v1["config"]["project_id"] == v1["id"]
    saved = await client.put(
        f"{P}/targets/{v1['id']}/execution",
        headers=h(dev),
        json={"secrets": {"backend": {"DB_PASSWORD": "test-only-password-1234"}}},
    )
    assert saved.status_code == 200, saved.text
    first = await run_audit(client, dev, v1["id"], monkeypatch, vulnerable=True)

    v2 = await upload(client, dev, agent_zip("class Controller {}\n"), previous=v1["id"])
    assert v2["config"]["version"] == 2 and v2["config"]["project_id"] == v1["id"]
    assert v2["config"]["previous_target_id"] == v1["id"]
    inherited = (await client.get(f"{P}/targets/{v2['id']}/execution", headers=h(dev))).json()
    assert inherited["secret_names"] == ["backend:DB_PASSWORD"]
    async with session_scope() as db:
        old = await db.get(ExecutionSettings, uuid.UUID(v1["id"]))
        new = await db.get(ExecutionSettings, uuid.UUID(v2["id"]))
        ref = new.secret_refs["backend:DB_PASSWORD"]
        assert ref != old.secret_refs["backend:DB_PASSWORD"]  # 참조를 공유하지 않는다
        assert SecretStore().get(v2["id"] + ":backend:DB_PASSWORD", ref) == "test-only-password-1234"

    candidates = (await client.get(f"{P}/targets/{v2['id']}/baselines", headers=h(dev))).json()
    assert [c["id"] for c in candidates] == [first] and candidates[0]["version"] == 1

    second = await run_audit(client, dev, v2["id"], monkeypatch, vulnerable=False, baseline=first)
    audit = (await client.get(f"{P}/audits/{second}", headers=h(dev))).json()
    assert audit["baseline_audit_id"] == first

    result = (await client.get(f"{P}/audits/{second}/compare", headers=h(dev))).json()
    assert result["baseline"]["version"] == 1 and result["current"]["version"] == 2
    probes = {p["probe_id"]: p["status"] for p in result["probes"]}
    assert probes == {"UP-PATH-01": "BLOCKED", "UP-RAG-01": "BLOCKED"}
    assert result["benign"] == {"baseline": "PASS", "current": "PASS"}
    statuses = {(f["category"], f["status"]) for f in result["findings"]}
    assert ("SECRET_LEAK", "RESOLVED") in statuses or ("HARDCODED_SECRET", "RESOLVED") in statuses
    assert any(f["source"] == "agent" and f["status"] == "RESOLVED" for f in result["findings"])
    assert any("모의(mock)" in n for n in result["notes"])
    md = await client.get(f"{P}/audits/{second}/compare.md", headers=h(dev))
    assert md.status_code == 200 and "재검증에서 차단" in md.text

    # ADMIN·AUDITOR(전체 읽기)도 비교를 조회할 수 있다
    for role in ("ADMIN", "AUDITOR"):
        assert (await client.get(f"{P}/audits/{second}/compare", headers=h(users[role]))).status_code == 200


async def test_baseline_must_be_same_project_and_completed(
    client: httpx.AsyncClient, users: dict[str, Any], env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    dev = users["DEVELOPER"]
    a = await upload(client, dev, agent_zip(LEAK))
    other = await upload(client, dev, project_zip())
    done = await run_audit(client, dev, a["id"], monkeypatch, vulnerable=True)
    unrelated = await client.post(
        f"{P}/audits", headers=h(dev), json={"target_ids": [other["id"]], "mode": "code", "baseline_audit_id": done}
    )
    assert unrelated.status_code == 422 and "같은 프로젝트" in unrelated.text
    async with session_scope() as db:
        (await db.get(Audit, uuid.UUID(done))).status = "RUNNING"
    pending = await client.post(
        f"{P}/audits", headers=h(dev), json={"target_ids": [a["id"]], "mode": "code", "baseline_audit_id": done}
    )
    assert pending.status_code == 422 and "완료된 감사" in pending.text
    assert (await client.get(f"{P}/audits/{done}/compare", headers=h(dev))).status_code == 409
    bad_link = await client.post(
        f"{P}/targets/upload",
        headers=h(dev),
        files={"file": ("x.zip", project_zip())},
        data={"previous_target_id": a["linked_target_id"]},
    )
    assert bad_link.status_code == 422  # 에이전트 대상은 이전 버전이 될 수 없다
    foreign = await client.post(
        f"{P}/targets/upload",
        headers=h(users["ADMIN"]),
        files={"file": ("x.zip", project_zip())},
        data={"previous_target_id": a["id"]},
    )
    assert foreign.status_code == 201  # ADMIN 은 전체 접근
