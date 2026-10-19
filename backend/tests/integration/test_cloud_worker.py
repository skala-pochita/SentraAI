"""CloudRunner: DB 상태·이벤트 저장·승인 대기·재개·티켓 1건·기한 초과 (in-process MCP, 스크립트된 모델)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import func, select, update

from core.cloud.llm import StageRunner
from core.db.models import CloudApproval, CloudTask, CloudTaskEvent, RemediationTicket, User
from core.db.session import session_scope
from core.mcp_client import inprocess_gateway
from core.settings import get_settings
from tests.fakes_cloud import ScriptedModel
from tests.unit.test_cloud_checks import BASE
from tests.unit.test_cloud_pipeline import action_report, happy_script
from workers.cloud import CloudRunner, reap_expired


def factory(script: list[Any]) -> Any:
    def make(key: str, budget: Any) -> tuple[StageRunner, str]:
        return StageRunner(ScriptedModel(script), "fake-model", budget), "fake-model"

    return make


@pytest.fixture
async def new_task(db_ready: None, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()

    async def make() -> tuple[str, str]:
        bundle = {
            **BASE,
            "business_policy": {
                **BASE.get("business_policy", {}),
                "followup_allowed": True,
                "ticket_target": "platform-team",
            },
        }
        async with session_scope() as db:
            user = User(email=f"u{uuid.uuid4().hex[:6]}@t.io", password_hash="x", name="u", role="ADMIN")
            db.add(user)
            await db.flush()
            t = CloudTask(requested_by=user.id, bundle_id="dev-1", bundle=bundle, bundle_sha256="0", status="QUEUED")
            db.add(t)
            await db.flush()
            return str(t.id), str(user.id)

    return make


async def events(tid: str) -> list[str]:
    async with session_scope() as db:
        rows = await db.execute(
            select(CloudTaskEvent.type)
            .where(CloudTaskEvent.task_id == uuid.UUID(tid))
            .order_by(CloudTaskEvent.event_seq)
        )
        return list(rows.scalars())


async def test_flow_wait_approval_restart_resume_one_ticket(new_task: Any) -> None:
    tid, uid = await new_task()
    saver = MemorySaver()
    r1 = CloudRunner(saver, inprocess_gateway, factory(happy_script(report=action_report())))
    assert await r1.start(tid) == "WAITING_APPROVAL"
    async with session_scope() as db:
        task = await db.get(CloudTask, uuid.UUID(tid))
        assert task is not None and task.deadline_at is None and task.answer is None  # 보고서는 승인 후 저장
        ap = (await db.execute(select(CloudApproval).where(CloudApproval.task_id == task.id))).scalar_one()
        assert ap.status == "PENDING" and ap.args["target"] == "platform-team"
        approval_id = str(ap.id)
    assert "APPROVAL_REQUIRED" in await events(tid)
    # 아직 결정 전 재개 요청은 거부, 중복 start 는 무시
    from core.errors import AppError

    with pytest.raises(AppError):
        await r1.resume(tid, approval_id)
    assert await r1.start(tid) == "WAITING_APPROVAL"

    async with session_scope() as db:
        await db.execute(
            update(CloudApproval)
            .where(CloudApproval.id == uuid.UUID(approval_id))
            .values(status="APPROVED", decided_by=uuid.UUID(uid), decided_at=datetime.now(UTC))
        )
    # 워커 재시작: 새 CloudRunner + 같은 체크포인터로 같은 thread 재개
    r2 = CloudRunner(saver, inprocess_gateway, factory([{"unused": 1}]))
    assert await r2.resume(tid, approval_id) == "COMPLETED"
    assert await r2.resume(tid, approval_id) == "COMPLETED"  # 중복 재개는 무시

    async with session_scope() as db:
        task = await db.get(CloudTask, uuid.UUID(tid))
        assert task is not None and task.status == "COMPLETED" and task.report_version == 1
        assert task.answer["actionResult"]["status"] == "CREATED" and task.answer["findings"]
        assert task.answer["sources"]
        n = (
            await db.execute(
                select(func.count()).select_from(RemediationTicket).where(RemediationTicket.task_id == task.id)
            )
        ).scalar_one()
        assert n == 1
    ev = await events(tid)
    assert ev.index("DONE") == len(ev) - 1 and ev.count("DONE") == 1
    assert "TOOL_START" in ev and "TOOL_END" in ev


async def test_reject_completes_without_ticket(new_task: Any) -> None:
    tid, uid = await new_task()
    saver = MemorySaver()
    r = CloudRunner(saver, inprocess_gateway, factory(happy_script(report=action_report())))
    await r.start(tid)
    async with session_scope() as db:
        ap = (await db.execute(select(CloudApproval).where(CloudApproval.task_id == uuid.UUID(tid)))).scalar_one()
        ap.status, ap.decided_by = "REJECTED", uuid.UUID(uid)
        approval_id = str(ap.id)
    assert await r.resume(tid, approval_id) == "COMPLETED"
    async with session_scope() as db:
        task = await db.get(CloudTask, uuid.UUID(tid))
        assert task is not None and task.answer["actionResult"]["status"] == "REJECTED"
        n = (await db.execute(select(func.count()).select_from(RemediationTicket))).scalar_one()
        assert n == 0


async def test_failed_task_keeps_partial_result(new_task: Any) -> None:
    tid, _ = await new_task()
    from tests.fakes_cloud import RateLimitError

    def make(key: str, budget: Any) -> tuple[StageRunner, str]:
        r = StageRunner(ScriptedModel([RateLimitError()]), "fake", budget)
        r.model.backoff = [0.0, 0.0]
        return r, "fake"

    assert await CloudRunner(MemorySaver(), inprocess_gateway, make).start(tid) == "FAILED"
    async with session_scope() as db:
        task = await db.get(CloudTask, uuid.UUID(tid))
        assert task is not None and task.failure_code == "RATE_LIMITED" and task.answer["partial"] is True
    assert (await events(tid))[-1] == "FAILED"


async def test_reaper_fails_expired_running_but_not_waiting(new_task: Any) -> None:
    running, _ = await new_task()
    waiting, _ = await new_task()
    past = datetime.now(UTC) - timedelta(seconds=5)
    async with session_scope() as db:
        await db.execute(
            update(CloudTask).where(CloudTask.id == uuid.UUID(running)).values(status="RUNNING", deadline_at=past)
        )
        await db.execute(
            update(CloudTask)
            .where(CloudTask.id == uuid.UUID(waiting))
            .values(status="WAITING_APPROVAL", deadline_at=None)
        )
    assert running in await reap_expired()
    async with session_scope() as db:
        a = await db.get(CloudTask, uuid.UUID(running))
        b = await db.get(CloudTask, uuid.UUID(waiting))
        assert a is not None and b is not None
        assert (a.status, a.failure_code) == (
            "FAILED",
            "EXECUTION_DEADLINE_EXCEEDED",
        ) and b.status == "WAITING_APPROVAL"
    assert (await events(running))[-1] == "FAILED"
