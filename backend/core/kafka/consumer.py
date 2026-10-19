"""Kafka consumer 기반 (명세 11.3).

- enable_auto_commit=False, 처리 완료 후 수동 커밋 (at-least-once)
- 멱등: kafka:processed:{message_id} — 처리 중엔 lease("processing:<worker>", 60s, 하트비트 갱신),
  완료 후 "done" 24h. 워커가 처리 중 죽으면 lease 만료 후 재전달 메시지를 이어받는다 (DECISIONS #6).
- 실패: retryable → jobs.retry (retry_count<3), 그 외·초과 → jobs.dlq
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from aiokafka import AIOKafkaConsumer, ConsumerRecord

from core.errors import AppError, ErrorCode
from core.kafka.envelope import Envelope, Headers, headers_from_kafka, retry_headers
from core.kafka.producer import publish
from core.redis.client import get_redis
from core.settings import get_settings

log = logging.getLogger("kafka.consumer")

LEASE_S = 60
DONE_TTL_S = 86400
WORKER_ID = f"{socket.gethostname()}-{uuid.uuid4().hex[:6]}"


@dataclass
class Message:
    topic: str
    key: str
    envelope: Envelope
    headers: Headers
    raw: bytes


Handler = Callable[[Message], Awaitable[None]]


async def _acquire(message_id: str) -> bool:
    """True = 처리 시작, False = 이미 완료(skip). 다른 워커가 처리 중이면 lease 만료까지 대기."""
    r = get_redis()
    key = f"kafka:processed:{message_id}"
    while True:
        if await r.set(key, f"processing:{WORKER_ID}", nx=True, ex=LEASE_S):
            return True
        val = await r.get(key)
        if val == "done":
            return False
        if val == f"processing:{WORKER_ID}":
            return True
        await asyncio.sleep(5)


async def _heartbeat(message_id: str) -> None:
    r = get_redis()
    while True:
        await asyncio.sleep(LEASE_S / 3)
        await r.set(f"kafka:processed:{message_id}", f"processing:{WORKER_ID}", xx=True, ex=LEASE_S)


async def route_failure(msg: Message, err: AppError) -> None:
    hdrs, to_dlq = retry_headers(msg.headers, msg.topic, str(err.code), err.message)
    if not err.retryable:
        to_dlq = True
    target = "jobs.dlq" if to_dlq else "jobs.retry"
    await publish(target, msg.key, msg.raw, hdrs)
    log.warning(
        "message %s → %s (code=%s retry=%s)", msg.envelope.message_id, target, err.code, hdrs.get("retry_count")
    )


async def handle_record(rec: ConsumerRecord, handler: Handler) -> None:
    raw = rec.value or b""
    try:
        env = Envelope.decode(raw)
    except ValueError:
        log.error("undecodable message on %s offset=%s → DLQ", rec.topic, rec.offset)
        await publish(
            "jobs.dlq",
            (rec.key or b"").decode(),
            raw,
            {
                "original_topic": rec.topic,
                "retry_count": "0",
                "last_error_code": "VALIDATION",
                "last_error_message": "undecodable envelope",
            },
        )
        return
    msg = Message(rec.topic, (rec.key or b"").decode(), env, headers_from_kafka(rec.headers), raw)
    if not await _acquire(env.message_id):
        log.info("skip duplicate message %s", env.message_id)
        return
    hb = asyncio.create_task(_heartbeat(env.message_id))
    try:
        await handler(msg)
    except AppError as e:
        await route_failure(msg, e)
    except Exception as e:  # noqa: BLE001 - 예상 밖 오류는 INTERNAL 로 DLQ (로그 남김)
        log.exception("handler crashed for %s", env.message_id)
        await route_failure(msg, AppError(ErrorCode.INTERNAL, f"{type(e).__name__}: {e}"[:300]))
    finally:
        hb.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hb
    await get_redis().set(f"kafka:processed:{env.message_id}", "done", ex=DONE_TTL_S)


async def run_consumer(
    topics: list[str], group_id: str, handler: Handler, on_idle: Callable[[], None] | None = None
) -> None:
    consumer = AIOKafkaConsumer(
        *topics,
        bootstrap_servers=get_settings().kafka_bootstrap,
        group_id=group_id,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        max_poll_interval_ms=1_800_000,
        session_timeout_ms=30000,
        heartbeat_interval_ms=10000,
    )
    await consumer.start()
    log.info("consumer started group=%s topics=%s", group_id, topics)
    try:
        while True:
            batch = await consumer.getmany(timeout_ms=1000, max_records=1)
            if on_idle:
                on_idle()
            for _tp, records in batch.items():
                for rec in records:
                    await handle_record(rec, handler)
                    await consumer.commit()
    finally:
        await consumer.stop()
