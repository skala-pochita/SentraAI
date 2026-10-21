"""재시도·DLQ (명세 11.3): retryable → jobs.retry(최대 3회, 백오프 헤더) → jobs.dlq, 비재시도 즉시 DLQ,
중복 메시지 skip, DLQ 적재·재처리(replay), 장애 주입(FAULT_INJECTION) 후 복구."""

from __future__ import annotations

import json
import time
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from api.seed import seed_users
from core.db.models import DlqMessage
from core.db.session import session_scope
from core.errors import AppError, ErrorCode
from core.kafka import consumer as kc
from core.kafka.envelope import Envelope, headers_to_kafka
from core.settings import REPO_ROOT, get_settings
from workers.retry import store_dlq

sent: list[tuple[str, str, bytes, dict[str, str]]] = []


@pytest.fixture(autouse=True)
def capture(monkeypatch: pytest.MonkeyPatch, fake_redis: object) -> None:
    sent.clear()

    async def fake_publish(topic: str, key: str, value: Any, headers: Any = None) -> None:
        raw = value.encode() if hasattr(value, "encode") and not isinstance(value, bytes) else value
        sent.append((topic, key, raw, dict(headers or {})))

    monkeypatch.setattr(kc, "publish", fake_publish)
    monkeypatch.setattr("api.routers.ops.publish", fake_publish)


def record(env: Envelope, headers: dict[str, str] | None = None) -> Any:
    return SimpleNamespace(
        topic="audit.requested", key=b"k", value=env.encode(), offset=1, headers=headers_to_kafka(headers or {})
    )


def env() -> Envelope:
    return Envelope(type="audit.requested", payload={"audit_id": str(uuid.uuid4())})


async def test_retryable_error_goes_to_retry_with_backoff_then_dlq() -> None:
    async def boom(msg: kc.Message) -> None:
        raise AppError(ErrorCode.RATE_LIMITED, "rate limited")

    e = env()
    hdr: dict[str, str] = {}
    targets = []
    for _ in range(4):
        sent.clear()
        await kc.handle_record(record(e, hdr), boom)
        topic, _, raw, hdr = sent[-1]
        targets.append(topic)
        assert Envelope.decode(raw).message_id == e.message_id  # 같은 메시지를 그대로 재발행
    assert targets == ["jobs.retry", "jobs.retry", "jobs.retry", "jobs.dlq"]
    assert (
        hdr["retry_count"] == "3"
        and hdr["last_error_code"] == "RATE_LIMITED"
        and hdr["original_topic"] == "audit.requested"
    )


async def test_backoff_headers_grow() -> None:
    async def boom(msg: kc.Message) -> None:
        raise AppError(ErrorCode.UPSTREAM_TIMEOUT, "t")

    e, hdr, delays = env(), {}, []
    for _ in range(3):
        sent.clear()
        await kc.handle_record(record(e, hdr), boom)
        hdr = sent[-1][3]
        delays.append(int(hdr["next_attempt_at"]) - int(time.time() * 1000))
    assert delays[0] < delays[1] < delays[2] and delays[0] > 0


async def test_non_retryable_goes_straight_to_dlq() -> None:
    async def bad(msg: kc.Message) -> None:
        raise AppError(ErrorCode.VALIDATION, "bad input")

    await kc.handle_record(record(env()), bad)
    assert [s[0] for s in sent] == ["jobs.dlq"] and sent[0][3]["last_error_code"] == "VALIDATION"


async def test_unexpected_exception_and_undecodable_message_go_to_dlq() -> None:
    async def crash(msg: kc.Message) -> None:
        raise RuntimeError("kaboom")

    await kc.handle_record(record(env()), crash)
    assert sent[-1][0] == "jobs.dlq" and sent[-1][3]["last_error_code"] == "INTERNAL"
    sent.clear()
    junk = SimpleNamespace(topic="audit.requested", key=b"k", value=b"not json", offset=2, headers=[])
    await kc.handle_record(junk, crash)
    assert sent[0][0] == "jobs.dlq" and sent[0][3]["last_error_message"] == "undecodable envelope"


async def test_duplicate_delivery_is_skipped_after_success() -> None:
    calls = 0

    async def ok(msg: kc.Message) -> None:
        nonlocal calls
        calls += 1

    e = env()
    await kc.handle_record(record(e), ok)
    await kc.handle_record(record(e), ok)  # 같은 message_id 재전달
    assert calls == 1 and not sent


async def test_dlq_store_and_replay_republishes_with_new_message_id(db_ready: None, client: httpx.AsyncClient) -> None:
    e = env()
    hdr = {
        "original_topic": "audit.requested",
        "retry_count": "3",
        "last_error_code": "RATE_LIMITED",
        "last_error_message": "x",
    }
    await store_dlq("jobs.dlq", b"k", e.encode(), headers_to_kafka(hdr))
    async with session_scope() as db:
        users = await seed_users(db)
        row = (await db.execute(select(DlqMessage))).scalars().one()
        assert row.status == "NEW" and row.error_code == "RATE_LIMITED" and row.original_topic == "audit.requested"
        dlq_id = str(row.id)
        assert users["ADMIN"]
    s = get_settings()
    tok = (
        await client.post("/api/v1/auth/login", json={"email": "admin@demo.io", "password": s.seed_admin_password})
    ).json()["access_token"]
    res = await client.post(f"/api/v1/ops/dlq/{dlq_id}/replay", headers={"Authorization": f"Bearer {tok}"})
    assert res.status_code == 200 and res.json()["status"] == "REPLAYED"
    topic, _, raw, headers = sent[-1]
    assert topic == "audit.requested" and headers["retry_count"] == "0"
    assert json.loads(raw)["message_id"] != e.message_id  # 멱등 키 충돌 방지
    again = await client.post(f"/api/v1/ops/dlq/{dlq_id}/replay", headers={"Authorization": f"Bearer {tok}"})
    assert again.status_code == 409


async def test_fault_injection_rate_limits_first_n_then_recovers(
    db_ready: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mcp_servers.business.tools import execute

    monkeypatch.setenv("FAULT_INJECTION", "github_rate_limit:2")  # 과거 형식도 인식
    get_settings.cache_clear()
    assert execute.fault_count() == 2
    monkeypatch.setenv("FAULT_INJECTION", "rate_limit:2")
    get_settings.cache_clear()
    from mcp_servers.business.workspace import TargetInfo, clear_overrides, register_override
    from mcp_servers.common import CallContext, default_call

    tid = "00000000-0000-0000-0000-0000000000fa"
    register_override(TargetInfo(tid, "repo", "x", str(REPO_ROOT / "targets" / "vuln-spring-app")))
    token = default_call.set(CallContext("u1", "ADMIN", str(uuid.uuid4()), "r"))
    try:
        codes = []
        for _ in range(3):  # 노드 안 인라인 재시도 3회와 같은 횟수
            res = await execute.run_static_scan(target_id=tid)
            codes.append(res.get("error", {}).get("code", "OK"))
        assert codes == ["RATE_LIMITED", "RATE_LIMITED", "OK"]
    finally:
        default_call.reset(token)
        clear_overrides()
        monkeypatch.setenv("FAULT_INJECTION", "")
        get_settings.cache_clear()
    assert execute.fault_count() == 0
