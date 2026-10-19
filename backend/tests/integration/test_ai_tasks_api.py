"""/api/ai/tasks: 인증·권한·격리, 202 접수, 조회 {status, answer, sources}, 승인 202(해시·중복·상태), SSE 이벤트."""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from api.seed import seed_users
from core.db.models import CloudApproval, CloudTask, CloudTaskEvent
from core.db.session import session_scope
from core.settings import get_settings
from tests.unit.test_cloud_checks import BASE

P = "/api/ai"
published: list[tuple[str, str, dict[str, Any]]] = []


@pytest.fixture(autouse=True)
def capture_kafka(monkeypatch: pytest.MonkeyPatch) -> None:
    published.clear()

    async def fake_publish(topic: str, key: str, envelope: Any, headers: Any = None) -> None:
        published.append((topic, key, envelope.payload))

    monkeypatch.setattr("api.routers.ai_tasks.publish", fake_publish)


@pytest.fixture
async def users(client: httpx.AsyncClient) -> dict[str, dict[str, str]]:
    async with session_scope() as db:
        await seed_users(db)
    s = get_settings()
    out = {}
    for role, email, pw in (
        ("ADMIN", "admin@demo.io", s.seed_admin_password),
        ("DEVELOPER", "dev@demo.io", s.seed_dev_password),
        ("AUDITOR", "auditor@demo.io", s.seed_auditor_password),
    ):
        res = await client.post("/api/v1/auth/login", json={"email": email, "password": pw})
        assert res.status_code == 200, res.text
        out[role] = {"Authorization": f"Bearer {res.json()['access_token']}"}
    return out


async def create(client: httpx.AsyncClient, hdr: dict[str, str]) -> str:
    res = await client.post(f"{P}/tasks", json={"evidence_bundle": BASE}, headers=hdr)
    assert res.status_code == 202, res.text
    return str(res.json()["taskId"])


async def test_auth_and_role_rules(client: httpx.AsyncClient, users: dict[str, dict[str, str]]) -> None:
    assert (await client.post(f"{P}/tasks", json={"evidence_bundle": BASE})).status_code == 401
    assert (
        await client.post(f"{P}/tasks", json={"evidence_bundle": BASE}, headers=users["AUDITOR"])
    ).status_code == 403
    tid = await create(client, users["DEVELOPER"])
    assert published[-1][0] == "audit.requested" and published[-1][2]["task_id"] == tid


async def test_invalid_bundle_rejected_with_422(client: httpx.AsyncClient, users: dict[str, dict[str, str]]) -> None:
    bad = {**BASE, "findings": [{"id": "x"}]}
    res = await client.post(f"{P}/tasks", json={"evidence_bundle": bad}, headers=users["DEVELOPER"])
    assert res.status_code == 422 and res.json()["error"]["code"] == "VALIDATION"


async def test_task_isolation_and_status_shape(client: httpx.AsyncClient, users: dict[str, dict[str, str]]) -> None:
    tid = await create(client, users["DEVELOPER"])
    own = (await client.get(f"{P}/tasks/{tid}", headers=users["DEVELOPER"])).json()
    assert own["status"] == "QUEUED" and own["answer"] is None and own["sources"] == []
    assert (await client.get(f"{P}/tasks/{tid}", headers=users["ADMIN"])).status_code == 200
    # 다른 사용자(AUDITOR)는 존재 여부도 알 수 없다
    assert (await client.get(f"{P}/tasks/{tid}", headers=users["AUDITOR"])).status_code == 404
    assert (await client.get(f"{P}/tasks/{tid}/stream", headers=users["AUDITOR"])).status_code == 404
    mine = (await client.get(f"{P}/tasks?mine=true", headers=users["DEVELOPER"])).json()
    assert [t["taskId"] for t in mine] == [tid] and set(mine[0]) == {"taskId", "status", "createdAt", "updatedAt"}
    assert (await client.get(f"{P}/tasks?mine=true", headers=users["AUDITOR"])).json() == []


async def make_waiting(tid: str) -> tuple[str, str]:
    args = {"title": "t", "target": "platform-team", "body": "b"}
    from core.cloud.tickets import args_hash

    async with session_scope() as db:
        task = await db.get(CloudTask, uuid.UUID(tid))
        assert task is not None
        task.status = "WAITING_APPROVAL"
        ap = CloudApproval(
            task_id=task.id,
            action_id="act-1",
            tool="business_create_remediation_ticket",
            args=args,
            args_hash=args_hash(args),
        )
        db.add(ap)
        await db.flush()
        return str(ap.id), args_hash(args)


async def test_pending_action_snapshot_and_approval_rules(
    client: httpx.AsyncClient, users: dict[str, dict[str, str]]
) -> None:
    tid = await create(client, users["DEVELOPER"])
    ref, h = await make_waiting(tid)
    snap = (await client.get(f"{P}/tasks/{tid}", headers=users["DEVELOPER"])).json()
    pa = snap["answer"]["pendingAction"]
    assert pa["actionId"] == "act-1" and pa["argsHash"] == h and pa["tool"] == "business_create_remediation_ticket"

    body = {"actionId": "act-1", "decision": "APPROVE", "argsHash": h}
    # 요청자(DEVELOPER)는 승인할 수 없다
    assert (await client.post(f"{P}/tasks/{tid}/approval", json=body, headers=users["DEVELOPER"])).status_code == 403
    # 해시 불일치 → 거부, 상태 변경 없음
    bad = await client.post(f"{P}/tasks/{tid}/approval", json={**body, "argsHash": "0" * 64}, headers=users["ADMIN"])
    assert bad.status_code == 409
    n_before = len(published)
    ok = await client.post(f"{P}/tasks/{tid}/approval", json=body, headers=users["ADMIN"])
    assert ok.status_code == 202 and ok.json() == {"approvalRef": ref, "status": "APPROVED"}
    assert published[-1] == ("audit.resume", tid, {"task_id": tid, "kind": "cloud_approval", "approval_id": ref})
    # 같은 결정 재전송은 202 로 resume 만 다시 보낸다, 반대 결정은 409
    again = await client.post(f"{P}/tasks/{tid}/approval", json=body, headers=users["ADMIN"])
    assert again.status_code == 202 and len(published) == n_before + 2
    flip = await client.post(f"{P}/tasks/{tid}/approval", json={**body, "decision": "REJECT"}, headers=users["ADMIN"])
    assert flip.status_code == 409


async def test_approval_rejected_when_task_not_waiting(
    client: httpx.AsyncClient, users: dict[str, dict[str, str]]
) -> None:
    tid = await create(client, users["ADMIN"])
    _, h = await make_waiting(tid)
    async with session_scope() as db:
        task = await db.get(CloudTask, uuid.UUID(tid))
        assert task is not None
        task.status = "COMPLETED"
    res = await client.post(
        f"{P}/tasks/{tid}/approval",
        json={"actionId": "act-1", "decision": "APPROVE", "argsHash": h},
        headers=users["ADMIN"],
    )
    assert res.status_code == 409


async def test_sse_streams_public_events_until_done(
    client: httpx.AsyncClient, users: dict[str, dict[str, str]]
) -> None:
    tid = await create(client, users["DEVELOPER"])
    async with session_scope() as db:
        for seq, (t, d) in enumerate(
            [("TOOL_START", {"tool": "x"}), ("INTERNAL_DEBUG", {}), ("TOKEN", {"text": "hi"}), ("DONE", {})], start=1
        ):
            db.add(CloudTaskEvent(task_id=uuid.UUID(tid), event_seq=seq, type=t, data=d))
    seen: list[dict[str, Any]] = []
    async with client.stream("GET", f"{P}/tasks/{tid}/stream", headers=users["DEVELOPER"]) as r:
        assert r.status_code == 200
        async for line in r.aiter_lines():
            if line.startswith("data:"):
                seen.append(json.loads(line[5:]))
    assert [e["type"] for e in seen] == ["TOOL_START", "TOKEN", "DONE"]
    assert set(seen[0]) == {"taskId", "eventSeq", "type", "timestamp", "data", "trace_id"}
    # 재접속: after 이후만
    tail: list[str] = []
    async with client.stream("GET", f"{P}/tasks/{tid}/stream?after=3", headers=users["DEVELOPER"]) as r:
        async for line in r.aiter_lines():
            if line.startswith("event:"):
                tail.append(line[6:].strip())
    assert tail == ["DONE"]


async def test_event_seq_unique_constraint(client: httpx.AsyncClient, users: dict[str, dict[str, str]]) -> None:
    from sqlalchemy.exc import IntegrityError

    tid = await create(client, users["DEVELOPER"])
    with pytest.raises(IntegrityError):
        async with session_scope() as db:
            for _ in range(2):
                db.add(CloudTaskEvent(task_id=uuid.UUID(tid), event_seq=1, type="TOKEN", data={}))
    async with session_scope() as db:
        rows = (
            (await db.execute(select(CloudTaskEvent).where(CloudTaskEvent.task_id == uuid.UUID(tid)))).scalars().all()
        )
        assert rows == []
