"""audit-worker(AuditRunner) 와 감사 결과 API 전 구간 (LLM 없음, in-process MCP, InMemorySaver).

업로드 → POST /audits → (Kafka 대신 캡처한 메시지로) AuditRunner.handle → 결과·보고서·SSE·트레이스 조회,
취소·실패·재전달 skip, 역할별 Finding 상태 변경.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from langgraph.checkpoint.memory import InMemorySaver

from core.db.models import Audit
from core.db.session import session_scope
from core.errors import AppError, ErrorCode
from core.graph.deps import Deps
from core.kafka.consumer import Message
from core.kafka.envelope import Envelope
from core.mcp_client import inprocess_gateway
from tests.integration.test_auth import P, h
from tests.integration.test_upload_audit import project_zip, users  # noqa: F401 - fixture
from workers.audit import AuditRunner


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Envelope]]:
    sent: list[tuple[str, Envelope]] = []

    async def fake_publish(topic: str, key: str, env: Envelope, headers: Any = None) -> None:
        sent.append((topic, env))

    async def no_sandbox(path: str, payload: Any = None, timeout: float = 30) -> dict[str, Any]:
        raise AppError(ErrorCode.UPSTREAM_ERROR, "sandbox-runner not available in tests")

    monkeypatch.setattr("api.routers.audits.publish", fake_publish)
    monkeypatch.setattr("workers.audit.publish", fake_publish)
    monkeypatch.setattr("core.sandbox_client.sandbox_call", no_sandbox)
    monkeypatch.setattr("api.routers.results.http_gateway", inprocess_gateway)
    return sent


def runner() -> AuditRunner:
    return AuditRunner(
        InMemorySaver(), gateway_factory=inprocess_gateway, deps_factory=lambda gw: Deps(gateway=gw, emit_events=True)
    )


def message(topic: str, audit_id: str) -> Message:
    env = Envelope(type=topic, payload={"audit_id": audit_id})
    return Message(topic=topic, key=audit_id, envelope=env, headers={}, raw=b"")


async def upload_and_request(client: httpx.AsyncClient, tok: dict[str, Any]) -> tuple[str, str]:
    up = await client.post(
        f"{P}/targets/upload", headers=h(tok), files={"file": ("source.zip", project_zip(), "application/zip")}
    )
    assert up.status_code == 201, up.text
    target_id = up.json()["id"]
    res = await client.post(f"{P}/audits", headers=h(tok), json={"target_ids": [target_id], "mode": "code"})
    assert res.status_code == 202, res.text
    return target_id, res.json()["audit_id"]


async def test_worker_runs_audit_and_results_api(
    client: httpx.AsyncClient,
    users: dict[str, Any],  # noqa: F811
    published: list[tuple[str, Envelope]],
) -> None:
    dev, admin, auditor = users["DEVELOPER"], users["ADMIN"], users["AUDITOR"]
    _, audit_id = await upload_and_request(client, dev)
    assert published[-1][0] == "audit.requested"

    # 완료 전: 결과 조회는 409, 진행 정보는 공개
    assert (await client.get(f"{P}/audits/{audit_id}/findings", headers=h(dev))).status_code == 409
    queued = (await client.get(f"{P}/audits/{audit_id}", headers=h(dev))).json()
    assert queued["status"] == "QUEUED" and queued["summary"] == {}

    r = runner()
    await r.handle(message("audit.requested", audit_id))
    assert published[-1][0] == "audit.completed"
    await r.handle(message("audit.resume", audit_id))  # 과거 토픽은 무시
    assert await r.run(audit_id) == "COMPLETED"  # 재전달은 skip

    detail = (await client.get(f"{P}/audits/{audit_id}", headers=h(dev))).json()
    assert detail["status"] == "COMPLETED" and detail["summary"]["total"] >= 1
    listing = (await client.get(f"{P}/audits", headers=h(auditor))).json()
    assert any(a["id"] == audit_id for a in listing["items"])

    findings = (await client.get(f"{P}/audits/{audit_id}/findings", headers=h(dev))).json()
    assert findings, "업로드 ZIP 의 가짜 비밀값이 탐지돼야 한다"
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
    assert [order[f["severity"]] for f in findings] == sorted(order[f["severity"]] for f in findings)
    assert "UploadFakePassword123!" not in str(findings)
    hi = (await client.get(f"{P}/audits/{audit_id}/findings", headers=h(dev), params={"source": "code"})).json()
    assert all(f["source"] == "code" for f in hi)

    fid = findings[0]["id"]
    one = (await client.get(f"{P}/findings/{fid}", headers=h(auditor))).json()
    assert one["id"] == fid and "risk_paths" in one

    # 역할별 상태 변경: DEVELOPER 는 FIXING 만, AUDITOR 는 불가, 사유 없는 오탐 신고는 VALIDATION
    assert (await client.patch(f"{P}/findings/{fid}", headers=h(dev), json={"status": "ACCEPTED_RISK"})).status_code == 403
    ok = await client.patch(f"{P}/findings/{fid}", headers=h(dev), json={"status": "FIXING"})
    assert ok.status_code == 200 and ok.json()["status"] == "FIXING"
    assert (await client.patch(f"{P}/findings/{fid}", headers=h(auditor), json={"status": "FIXING"})).status_code == 403
    bad = await client.patch(f"{P}/findings/{fid}", headers=h(admin), json={"false_positive": {"reason": " "}})
    assert bad.status_code == 422
    assert (await client.patch(f"{P}/findings/{fid}", headers=h(admin), json={})).status_code == 422
    missing = await client.patch(f"{P}/findings/{uuid.uuid4()}", headers=h(admin), json={"status": "OPEN"})
    assert missing.status_code == 404

    assert isinstance((await client.get(f"{P}/audits/{audit_id}/risk-paths", headers=h(dev))).json(), list)
    probes = (await client.get(f"{P}/audits/{audit_id}/probes", headers=h(dev))).json()
    assert probes["probes"] == [] and probes["asr_by_category"] == {}
    report = (await client.get(f"{P}/audits/{audit_id}/report", headers=h(auditor))).json()
    assert report["report_id"]
    md = await client.get(f"{P}/audits/{audit_id}/report.md", headers=h(auditor))
    assert md.status_code == 200 and md.headers["content-disposition"].startswith("attachment")

    # SSE: 종료 이벤트까지 재생하고 닫힌다. 상세 결과 payload 는 비운다
    sse = await client.get(f"{P}/audits/{audit_id}/events", headers=h(dev))
    assert sse.status_code == 200 and "audit.completed" in sse.text and '"payload": {}' in sse.text

    spans = await client.get(f"{P}/traces", headers=h(admin), params={"audit_id": audit_id})
    assert spans.status_code == 200 and spans.json()
    assert (await client.get(f"{P}/traces", headers=h(admin))).status_code == 422
    assert (await client.get(f"{P}/traces", headers=h(dev), params={"audit_id": audit_id})).status_code == 403
    assert (await client.post(f"{P}/audits/{audit_id}/cancel", headers=h(admin))).status_code == 409


async def test_cancel_failure_and_validation(
    client: httpx.AsyncClient,
    users: dict[str, Any],  # noqa: F811
    published: list[tuple[str, Envelope]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dev, admin = users["DEVELOPER"], users["ADMIN"]
    target_id, audit_id = await upload_and_request(client, dev)

    # 업로드 소스만으로 full/agent 감사는 준비 조건 오류
    for mode in ("full", "agent"):
        res = await client.post(f"{P}/audits", headers=h(dev), json={"target_ids": [target_id], "mode": mode})
        assert res.status_code == 422, (mode, res.text)
    missing = await client.post(f"{P}/audits", headers=h(dev), json={"target_ids": [str(uuid.uuid4())], "mode": "code"})
    assert missing.status_code == 404

    # 취소: ADMIN 만, 샌드박스 정리 실패는 TTL 정리기로 넘기고 성공 처리
    assert (await client.post(f"{P}/audits/{audit_id}/cancel", headers=h(dev))).status_code == 403
    res = await client.post(f"{P}/audits/{audit_id}/cancel", headers=h(admin))
    assert res.status_code == 200 and res.json()["status"] == "CANCELLED"
    assert await runner().run(audit_id) == "CANCELLED"

    with pytest.raises(AppError) as e:
        await runner().run(str(uuid.uuid4()))
    assert e.value.code == ErrorCode.NOT_FOUND

    # 그래프 실패 → FAILED + 대상 락 해제
    _, failing = await upload_and_request(client, dev)
    r = runner()

    async def boom(*_: Any, **__: Any) -> None:
        raise AppError(ErrorCode.VALIDATION, "boom")

    monkeypatch.setattr(r.graph, "ainvoke", boom)
    with pytest.raises(AppError):
        await r.run(failing)
    async with session_scope() as db:
        a = await db.get(Audit, uuid.UUID(failing))
        assert a is not None and a.status == "FAILED" and a.finished_at is not None
