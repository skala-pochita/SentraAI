"""Phase 1 완료 조건: 역할별 접근 허용·거부, 만료/블랙리스트 401, 타인 대상 403."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest

from api.seed import seed_targets, seed_users
from core.db.models import Target
from core.db.session import session_scope
from core.settings import get_settings

P = "/api/v1"


@pytest.fixture
async def seeded(client: httpx.AsyncClient) -> dict[str, Any]:
    async with session_scope() as db:
        users = await seed_users(db)
        repo, agent = await seed_targets(db, users["DEVELOPER"])
        other = Target(type="repo", name="someone-else", ref="/workspace/other")
        db.add(other)
        await db.flush()
        return {"users": users, "repo": repo.id, "agent": agent.id, "other": other.id}


async def login(client: httpx.AsyncClient, email: str, password: str) -> dict[str, Any]:
    res = await client.post(f"{P}/auth/login", json={"email": email, "password": password})
    assert res.status_code == 200, res.text
    data: dict[str, Any] = res.json()
    return data


def h(tok: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok['access_token']}"}


async def tokens(client: httpx.AsyncClient) -> dict[str, dict[str, Any]]:
    s = get_settings()
    return {
        "ADMIN": await login(client, "admin@demo.io", s.seed_admin_password),
        "DEVELOPER": await login(client, "dev@demo.io", s.seed_dev_password),
        "AUDITOR": await login(client, "auditor@demo.io", s.seed_auditor_password),
    }


async def test_login_me_and_bad_password(client: httpx.AsyncClient, seeded: dict[str, Any]) -> None:
    t = await tokens(client)
    me = await client.get(f"{P}/me", headers=h(t["DEVELOPER"]))
    assert me.status_code == 200 and me.json()["role"] == "DEVELOPER"
    bad = await client.post(f"{P}/auth/login", json={"email": "dev@demo.io", "password": "wrong"})
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "UNAUTHORIZED"


async def test_lockout_after_5_failures(client: httpx.AsyncClient, seeded: dict[str, Any]) -> None:
    for _ in range(5):
        await client.post(f"{P}/auth/login", json={"email": "auditor@demo.io", "password": "nope"})
    res = await client.post(
        f"{P}/auth/login", json={"email": "auditor@demo.io", "password": get_settings().seed_auditor_password}
    )
    assert res.status_code == 429


async def test_role_matrix_users(client: httpx.AsyncClient, seeded: dict[str, Any]) -> None:
    t = await tokens(client)
    assert (await client.get(f"{P}/users", headers=h(t["ADMIN"]))).status_code == 200
    assert (await client.get(f"{P}/users", headers=h(t["DEVELOPER"]))).status_code == 403
    assert (await client.get(f"{P}/users", headers=h(t["AUDITOR"]))).status_code == 403
    assert (await client.get(f"{P}/users")).status_code == 401
    body = {"type": "repo", "name": "x", "ref": "/x"}
    assert (await client.post(f"{P}/targets", json=body, headers=h(t["DEVELOPER"]))).status_code == 403
    assert (await client.post(f"{P}/targets", json=body, headers=h(t["ADMIN"]))).status_code == 422


async def test_developer_sees_only_own_targets(client: httpx.AsyncClient, seeded: dict[str, Any]) -> None:
    t = await tokens(client)
    dev = (await client.get(f"{P}/targets", headers=h(t["DEVELOPER"]))).json()
    ids = {x["id"] for x in dev["items"]}
    assert str(seeded["repo"]) in ids and str(seeded["other"]) not in ids
    admin = (await client.get(f"{P}/targets", headers=h(t["ADMIN"]))).json()
    assert admin["total"] == 3


async def test_expired_token_401(client: httpx.AsyncClient, seeded: dict[str, Any]) -> None:
    user = seeded["users"]["ADMIN"]
    past = datetime.now(UTC) - timedelta(minutes=1)
    tok = jwt.encode(
        {"sub": str(user.id), "role": "ADMIN", "jti": "x", "exp": past, "typ": "access"},
        get_settings().jwt_secret,
        algorithm="HS256",
    )
    res = await client.get(f"{P}/me", headers={"Authorization": f"Bearer {tok}"})
    assert res.status_code == 401


async def test_logout_blacklists_and_refresh_rotation(client: httpx.AsyncClient, seeded: dict[str, Any]) -> None:
    t = await tokens(client)
    adm = t["ADMIN"]
    r1 = await client.post(f"{P}/auth/refresh", json={"refresh_token": adm["refresh_token"]})
    assert r1.status_code == 200
    # 회전: 기존 refresh 재사용 불가
    r2 = await client.post(f"{P}/auth/refresh", json={"refresh_token": adm["refresh_token"]})
    assert r2.status_code == 401
    new = r1.json()
    out = await client.post(f"{P}/auth/logout", json={"refresh_token": new["refresh_token"]}, headers=h(new))
    assert out.status_code == 204
    assert (await client.get(f"{P}/me", headers=h(new))).status_code == 401
    r3 = await client.post(f"{P}/auth/refresh", json={"refresh_token": new["refresh_token"]})
    assert r3.status_code == 401
