"""진행 이벤트 emit (명세 8.7).

발행처: Redis Pub/Sub audit:{id}:events (실시간) + Redis Stream audit:{id}:stream (MAXLEN ~2000, SSE 재연결 재생)
+ audit:{id}:status Hash + DB audit_events (영구 보관). 이벤트 실패는 감사 자체를 멈추지 않고 경고 로그만 남긴다.
"""

from __future__ import annotations

import json
import logging
import uuid
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
    "probe_generate": 0.18,
    "probe_run": 0.28,
    "probe_judge": 0.35,
    "correlate": 0.45,
    "human_approval": 0.5,
    "risk_accept": 0.55,
    "fix_generate": 0.62,
    "apply_fix": 0.7,
    "wait_merge": 0.75,
    "re_verify": 0.85,
    "mark_manual": 0.9,
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


async def emit(
    audit_id: str,
    type_: str,
    payload: dict[str, Any] | None = None,
    node: str | None = None,
    progress: float | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    ev = make_event(audit_id, type_, payload, node, progress)
    data = json.dumps(ev, ensure_ascii=False, default=str)
    r = get_redis()
    try:
        await r.publish(f"audit:{audit_id}:events", data)
        await r.xadd(f"audit:{audit_id}:stream", {"data": data}, maxlen=STREAM_MAXLEN, approximate=True)
        await r.expire(f"audit:{audit_id}:stream", TTL_S)
        status: dict[str, str] = {"updated_at": ev["ts"], "last_event": type_}
        if node:
            status["node"] = node
        if ev["progress"] is not None:
            status["progress"] = str(ev["progress"])
        if type_.startswith("audit."):
            status["status"] = type_.split(".", 1)[1]
        await r.hset(f"audit:{audit_id}:status", mapping=status)  # type: ignore[arg-type]
        await r.expire(f"audit:{audit_id}:status", TTL_S)
    except (RedisError, OSError) as e:
        log.warning("event redis publish failed audit=%s type=%s err=%s", audit_id, type_, type(e).__name__)
    if persist:
        try:
            async with session_scope() as db:
                db.add(
                    AuditEvent(
                        id=uuid.UUID(ev["event_id"]),
                        audit_id=uuid.UUID(audit_id),
                        type=type_,
                        node=node,
                        payload=ev["payload"],
                        ts=datetime.fromisoformat(ev["ts"]),
                    )
                )
        except (SQLAlchemyError, OSError, ValueError) as e:
            log.warning("event db persist failed audit=%s type=%s err=%s", audit_id, type_, type(e).__name__)
    return ev
