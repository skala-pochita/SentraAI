from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

from core.db.models import Audit, ExecutionSettings, Target
from core.db.session import session_scope
from core.execution_config import ExecutionConfig
from core.proxy_sessions import issue, revoke, usage
from core.secret_store import SecretStore
from core.settings import get_settings
from tests.integration.test_auth import P, h
from tests.integration.test_upload_audit import project_zip

pytest_plugins = ["tests.integration.test_upload_audit"]


@pytest.fixture
async def project(
    client: httpx.AsyncClient, users: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    monkeypatch.setenv("VAULT_DATA_DIR", str(tmp_path / "ciphertext"))
    monkeypatch.setenv("VAULT_KEY_FILE", str(tmp_path / "keys/master.key"))
    get_settings.cache_clear()
    response = await client.post(
        f"{P}/targets/upload", headers=h(users["DEVELOPER"]), files={"file": ("source.zip", project_zip())}
    )
    assert response.status_code == 201
    return response.json()


async def test_execution_write_only_secrets_and_validation(
    client: httpx.AsyncClient, users: dict[str, Any], project: dict[str, Any]
) -> None:
    tid = project["id"]
    key = "test-provider-key-private-012345"
    secret = "test-database-password-private"
    response = await client.put(
        f"{P}/targets/{tid}/execution",
        headers=h(users["DEVELOPER"]),
        json={
            "mode": "live",
            "provider_api_key": key,
            "environment": {"agent": {"APP_MODE": "test"}},
            "secrets": {"backend": {"DB_PASSWORD": secret}},
        },
    )
    assert response.status_code == 200, response.text
    assert key not in response.text and secret not in response.text
    assert response.json()["provider_key_configured"] and response.json()["secret_names"] == ["backend:DB_PASSWORD"]
    saved = await client.get(f"{P}/targets/{tid}/execution", headers=h(users["DEVELOPER"]))
    assert key not in saved.text and secret not in saved.text
    async with session_scope() as db:
        row = await db.get(ExecutionSettings, uuid.UUID(tid))
        assert key not in json.dumps(row.config) + json.dumps(row.secret_refs)
        assert SecretStore().get(tid + ":provider", row.secret_refs["provider"]) == key
    invalid = await client.put(
        f"{P}/targets/{tid}/execution",
        headers=h(users["DEVELOPER"]),
        json={"max_requests": -1, "provider_api_key": key},
    )
    assert invalid.status_code == 422 and key not in invalid.text
    assert (await client.put(f"{P}/targets/{tid}/execution", headers=h(users["AUDITOR"]), json={})).status_code == 403
    removed = await client.put(
        f"{P}/targets/{tid}/execution",
        headers=h(users["DEVELOPER"]),
        json={"remove_provider_key": True, "remove_secrets": ["backend:DB_PASSWORD"]},
    )
    assert removed.status_code == 200 and not removed.json()["provider_key_configured"]
    assert removed.json()["secret_names"] == []


async def test_proxy_only_uses_provider_key_upstream_and_revokes(
    client: httpx.AsyncClient, users: dict[str, Any], project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from llm_proxy import server as proxy

    key = "test-provider-key-private-012345"
    target_id = uuid.UUID(project["id"])
    async with session_scope() as db:
        target = await db.get(Target, target_id)
        aid = uuid.uuid4()
        db.add(Audit(id=aid, requested_by=target.owner_user_id, mode="full", status="RUNNING", thread_id="proxy-test"))
    ref = SecretStore().put(str(target_id) + ":provider", key)
    config = ExecutionConfig(max_requests=4, max_total_tokens=10_000, max_output_tokens=128)
    token = await issue(str(aid), str(target_id), config, ref)
    calls = []

    async def upstream(payload: dict[str, Any], actual_key: str) -> httpx.Response:
        assert actual_key == key and token not in json.dumps(payload)
        assert payload["max_completion_tokens"] == 128 and payload["stream"] is False
        calls.append(payload)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"role": "assistant", "content": "verified"}}],
                "usage": {"total_tokens": 20},
            },
        )

    monkeypatch.setattr(proxy, "upstream_call", upstream)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy.app), base_url="http://proxy") as request:
        payload = {"model": config.model, "messages": [{"role": "user", "content": "hello"}], "max_tokens": 1000}
        response = await request.post(
            "/v1/chat/completions", headers={"Authorization": "Bearer " + token}, json=payload
        )
        assert response.status_code == 200 and key not in response.text and len(calls) == 1
        assert (await usage(str(aid)))["successful_requests"] == 1
        assert (
            await request.post(
                "/v1/chat/completions", headers={"Authorization": "Bearer " + token}, json=payload | {"stream": True}
            )
        ).status_code == 422
        assert (
            await request.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer " + token},
                json=payload | {"model": "forbidden"},
            )
        ).status_code == 403
        await revoke(str(aid))
        assert (
            await request.post("/v1/chat/completions", headers={"Authorization": "Bearer " + token}, json=payload)
        ).status_code == 401
    assert len(calls) == 1
