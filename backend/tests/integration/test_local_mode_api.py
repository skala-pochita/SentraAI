"""AUTH_MODE=local: 토큰 없이 고정 사용자로 동작하고, 외부 출처 요청은 403."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest

from api.local_mode import LOCAL_EMAIL
from core.settings import get_settings

P = "/api/v1"
LOCAL = {"x-sentra-local": "1"}


@pytest.fixture
async def local(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setenv("AUTH_MODE", "local")
    get_settings.cache_clear()
    from api.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost:5180") as c:
        yield c
    get_settings.cache_clear()


async def test_mode_endpoint(client: httpx.AsyncClient, local: httpx.AsyncClient) -> None:
    assert (await local.get(f"{P}/auth/mode")).json() == {"mode": "local"}


async def test_me_without_token_is_local_user(local: httpx.AsyncClient) -> None:
    res = await local.get(f"{P}/me")
    assert res.status_code == 200, res.text
    assert res.json()["email"] == LOCAL_EMAIL and res.json()["name"] == "User" and res.json()["role"] == "ADMIN"
    # 웹 모드에서 남은 토큰이 있어도 무시한다
    assert (await local.get(f"{P}/me", headers={"Authorization": "Bearer stale"})).status_code == 200


async def test_mutation_needs_local_header(local: httpx.AsyncClient) -> None:
    body = {"target_ids": [], "mode": "code"}
    assert (await local.post(f"{P}/audits", json=body)).status_code == 403
    # 헤더가 있으면 인증을 통과해 본문 검증까지 간다
    assert (await local.post(f"{P}/audits", json=body, headers=LOCAL)).status_code != 403


async def test_foreign_host_and_origin_rejected(local: httpx.AsyncClient) -> None:
    assert (await local.get(f"{P}/me", headers={"Host": "evil.example"})).status_code == 403
    assert (await local.get(f"{P}/me", headers={"Origin": "https://evil.example"})).status_code == 403


async def test_server_mode_unchanged(client: httpx.AsyncClient) -> None:
    assert (await client.get(f"{P}/auth/mode")).json() == {"mode": "server"}
    assert (await client.get(f"{P}/me")).status_code == 401
