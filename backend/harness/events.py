"""진행 이벤트 emit (명세 8.7).

발행처: Redis Pub/Sub audit:{id}:events (실시간) + Redis Stream audit:{id}:stream (MAXLEN ~2000, SSE 재연결 재생)
+ audit:{id}:status Hash + DB audit_events (영구 보관). 이벤트 실패는 감사 자체를 멈추지 않고 경고 로그만 남긴다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from core.db.models import AuditEvent
from core.db.session import session_scope
from core.redis.client import get_redis
from harness.masking import mask_obj

log = logging.getLogger("events")

STREAM_MAXLEN = 2000
TTL_S = 86400

# 노드 순서 기반 진행률 (대략)
NODE_PROGRESS = {
    "intake": 0.05,
    "code_static_scan": 0.12,
    "code_investigate": 0.25,
    "code_evidence_check": 0.32,
    "agent_static_check": 0.12,
    "prepare_sandbox": 0.18,
    "probe_generate": 0.18,
    "probe_run": 0.28,
    "probe_judge": 0.35,
    "correlate": 0.9,
    "report": 0.97,
}


def make_event(
    audit_id: str,
    type_: str,
    payload: dict[str, Any] | None = None,
    node: str | None = None,
    progress: float | None = None,
) -> dict[str, Any]:
    return {
        "event_id": str(uuid.uuid4()),
        "audit_id": audit_id,
        "type": type_,
        "node": node,
        "ts": datetime.now(UTC).isoformat(),
        "progress": progress if progress is not None else NODE_PROGRESS.get(node or "", None),
        "payload": mask_obj(payload or {}),
    }


async def _publish_redis(evs: list[dict[str, Any]]) -> None:
    """이벤트 묶음을 Redis 에 한 번의 pipeline 으로 기록한다 (Pub/Sub → Stream → status Hash 순서 유지)."""
    r = get_redis()
    try:
        pipe = r.pipeline(transaction=False)
        for ev in evs:
            if "__flush__" in ev:
                continue
            aid, type_, node = ev["audit_id"], ev["type"], ev["node"]
            data = json.dumps(ev, ensure_ascii=False, default=str)
            pipe.publish(f"audit:{aid}:events", data)
            pipe.xadd(f"audit:{aid}:stream", {"data": data}, maxlen=STREAM_MAXLEN, approximate=True)
            pipe.expire(f"audit:{aid}:stream", TTL_S)
            status: dict[str, str] = {"updated_at": ev["ts"], "last_event": type_}
            if node:
                status["node"] = node
            if ev["progress"] is not None:
                status["progress"] = str(ev["progress"])
            if type_.startswith("audit."):
                status["status"] = type_.split(".", 1)[1]
            pipe.hset(f"audit:{aid}:status", mapping=status)  # type: ignore[arg-type]
            pipe.expire(f"audit:{aid}:status", TTL_S)
        await pipe.execute()
    except (RedisError, OSError) as e:
        log.warning("event redis publish failed n=%d type=%s err=%s", len(evs), evs[-1]["type"], type(e).__name__)


async def _persist_db(evs: list[dict[str, Any]], rows: list[Any] | None = None) -> None:
    evs = [ev for ev in evs if "__flush__" not in ev]
    if not evs and not rows:
        return
    try:
        async with session_scope() as db:
            db.add_all(
                AuditEvent(
                    id=uuid.UUID(ev["event_id"]),
                    audit_id=uuid.UUID(ev["audit_id"]),
                    type=ev["type"],
                    node=ev["node"],
                    payload=ev["payload"],
                    ts=datetime.fromisoformat(ev["ts"]),
                )
                for ev in evs
            )
            db.add_all(rows or [])
    except (SQLAlchemyError, OSError, ValueError) as e:
        log.warning("event db persist failed n=%d rows=%d err=%s", len(evs), len(rows or []), type(e).__name__)


def current_buffer() -> EventBuffer | None:
    buf = _buffer.get()
    return buf if buf is not None and buf.accepting else None


class EventBuffer:
    """감사 실행 중 이벤트를 큐에 쌓고 백그라운드 task 가 묶어서 기록한다 (B3).

    emit 은 큐에 넣고 바로 돌아오므로 노드 실행이 Redis·DB 왕복을 기다리지 않는다. 기록 순서는 큐 순서와 같다.
    """

    MAX_BATCH = 200

    def __init__(self) -> None:
        self.queue: asyncio.Queue[tuple[dict[str, Any], bool] | None] = asyncio.Queue()
        self.rows: list[Any] = []  # 같은 묶음에 함께 insert 할 ORM 행 (trace span 등, B7)
        self.task: asyncio.Task[None] | None = None
        self.closing = False

    @property
    def accepting(self) -> bool:
        return self.task is not None and not self.task.done() and not self.closing

    def add_row(self, row: Any) -> None:
        """이벤트가 아닌 DB 행(trace span)을 다음 묶음 insert 에 싣는다. 기록 task 를 깨우려고 빈 신호를 넣지 않고
        다음 이벤트·flush·close 때 함께 기록한다 (flush 는 빈 묶음으로 깨운다)."""
        self.rows.append(row)

    def start(self) -> None:
        self.task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while True:
            item = await self.queue.get()
            batch = [item]
            while len(batch) < self.MAX_BATCH and not self.queue.empty():
                batch.append(self.queue.get_nowait())
            evs = [x for x in batch if x is not None]
            rows, self.rows = self.rows, []
            if evs:
                await _publish_redis([ev for ev, _ in evs])
            await _persist_db([ev for ev, persist in evs if persist], rows)
            for _ in batch:
                self.queue.task_done()
            if None in batch:  # 종료 신호
                return

    async def flush(self) -> None:
        if self.rows and self.accepting:
            self.queue.put_nowait(({"__flush__": True}, False))
        await self.queue.join()

    async def close(self) -> None:
        if self.task is None:
            return
        self.closing = True  # 이후 emit 은 직접 기록 → 종료 신호 뒤에 들어와 유실되는 이벤트 없음
        self.queue.put_nowait(None)
        await self.task


_buffer: ContextVar[EventBuffer | None] = ContextVar("event_buffer", default=None)


@asynccontextmanager
async def buffered_events() -> AsyncIterator[EventBuffer]:
    """이 블록 안(자식 task 포함)의 emit 을 비동기 일괄 기록으로 바꾼다. 블록을 나갈 때 모두 기록한다."""
    buf = EventBuffer()
    buf.start()
    token = _buffer.set(buf)
    try:
        yield buf
    finally:
        _buffer.reset(token)
        await buf.close()
        if buf.rows:  # 종료 신호 뒤에 쌓인 행
            await _persist_db([], buf.rows)
            buf.rows = []


async def emit(
    audit_id: str,
    type_: str,
    payload: dict[str, Any] | None = None,
    node: str | None = None,
    progress: float | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    ev = make_event(audit_id, type_, payload, node, progress)
    buf = _buffer.get()
    if buf is not None and buf.accepting:
        buf.queue.put_nowait((ev, persist))
        return ev
    await _publish_redis([ev])
    if persist:
        await _persist_db([ev])
    return ev
