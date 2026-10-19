"""Agent·Cloud: business-mcp/rag-mcp 도구 (실제 Postgres, in-process MCP). 승인·해시·멱등을 DB 에서 검증한다."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest

from core.cloud.backend_mcp import McpBackend
from core.cloud.budget import CloudBudget
from core.cloud.bundle import parse_bundle
from core.cloud.tickets import args_hash
from core.cloud.tools import ToolHost
from core.db.models import CloudApproval, CloudTask, User
from core.db.session import session_scope
from core.mcp_client import inprocess_gateway
from core.settings import get_settings
from mcp_servers.rag.ingest import ingest_dir
from tests.unit.test_cloud_checks import BASE

ARGS = {"title": "S3 권한 축소", "target": "platform-team", "body": "b"}


@pytest.fixture
async def task(db_ready: None, monkeypatch: pytest.MonkeyPatch) -> tuple[str, str]:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()
    async with session_scope() as db:
        user = User(email=f"u{uuid.uuid4().hex[:6]}@t.io", password_hash="x", name="u", role="ADMIN")
        db.add(user)
        await db.flush()
        t = CloudTask(
            requested_by=user.id, bundle_id="dev-1", bundle=BASE, bundle_sha256="0", status="RUNNING"
        )
        db.add(t)
        await db.flush()
        return str(t.id), str(user.id)


def backend(task_id: str, actor: str, role: str = "ADMIN") -> McpBackend:
    return McpBackend(inprocess_gateway(), task_id, actor, role)


async def test_evidence_through_mcp_and_access_control(task: tuple[str, str]) -> None:
    tid, uid = task
    ev = await backend(tid, uid).get_audit_evidence(["T1", "nope"], None)
    assert ev["items"][0]["evidence_id"] == "T1" and ev["not_found"] == ["nope"]
    # 다른 사용자(비 ADMIN/AUDITOR)는 접근 불가
    from core.errors import AppError

    with pytest.raises(AppError) as e:
        await backend(tid, str(uuid.uuid4()), "DEVELOPER").get_audit_evidence(None, None)
    assert e.value.code == "FORBIDDEN"


async def test_ticket_requires_approved_matching_hash_and_is_idempotent(task: tuple[str, str]) -> None:
    from core.errors import AppError

    tid, uid = task
    be = backend(tid, uid)
    action = "act-1"
    # 승인 없음
    with pytest.raises(AppError) as e:
        await be.create_remediation_ticket(tid, action, ARGS, None, None)
    assert e.value.code == "FORBIDDEN"
    # PENDING 승인 레코드는 무효
    async with session_scope() as db:
        ap = CloudApproval(task_id=uuid.UUID(tid), action_id=action, tool="business_create_remediation_ticket", args=ARGS, args_hash=args_hash(ARGS))
        db.add(ap)
        await db.flush()
        ref = str(ap.id)
    with pytest.raises(AppError) as e2:
        await be.create_remediation_ticket(tid, action, ARGS, ref, None)
    assert e2.value.code == "FORBIDDEN"
    async with session_scope() as db:
        row = await db.get(CloudApproval, uuid.UUID(ref))
        assert row is not None
        row.status, row.decided_by, row.decided_at = "APPROVED", uuid.UUID(uid), datetime.now(UTC)
    # 승인된 해시와 다른 인수 → 거부 (티켓 없음)
    with pytest.raises(AppError) as e3:
        await be.create_remediation_ticket(tid, action, {**ARGS, "target": "attacker"}, ref, None)
    assert e3.value.code == "FORBIDDEN"
    first = await be.create_remediation_ticket(tid, action, ARGS, ref, args_hash(ARGS))
    again = await be.create_remediation_ticket(tid, action, ARGS, ref, None)
    assert first["duplicate"] is False and again["duplicate"] is True and first["ticket_id"] == again["ticket_id"]
    with pytest.raises(AppError) as e4:
        await be.create_remediation_ticket(tid, action, {**ARGS, "title": "다른 제목"}, ref, None)
    assert e4.value.code == "CONFLICT"
    status = await be.get_finding_status(None)
    assert len(status["tickets"]) == 1


async def test_developer_cannot_call_mutating_tool(task: tuple[str, str]) -> None:
    from core.errors import AppError

    tid, uid = task
    with pytest.raises(AppError) as e:
        await backend(tid, uid, "DEVELOPER").create_remediation_ticket(tid, "a", ARGS, None, None)
    assert e.value.code == "FORBIDDEN"


async def test_resource_and_guidance_search_via_rag_mcp(task: tuple[str, str]) -> None:
    tid, uid = task
    async with session_scope() as db:
        await ingest_dir(db, get_settings().knowledge_dir)
    be = backend(tid, uid)
    res = await be.read_resource("guidance://security-baseline/v1")
    assert "최소 권한" in res["text"]
    hits = await be.search_guidance("IAM 최소 권한 PassRole", 3)
    assert hits and hits[0]["uri"] == "guidance://security-baseline/v1"
    # ToolHost 경유: 허용 URI 만, 작업당 3회
    host = ToolHost(be, CloudBudget.from_config(), tid, allowed_resource_uris={"guidance://security-baseline/v1"})
    ok = await host.call("read_resource", {"uri": "guidance://security-baseline/v1"})
    denied = await host.call("read_resource", {"uri": "guidance://other/v1"})
    assert ok.ok and denied.error_code == "FORBIDDEN"
    out = await host.call("business_get_audit_evidence", {"evidence_ids": ["T1"]})
    assert json.loads(out.content)["items"][0]["evidence_id"] == "T1"
    # parse_bundle 이 그대로 통과하는 입력인지(저장 형식 보존)
    parse_bundle(BASE)
