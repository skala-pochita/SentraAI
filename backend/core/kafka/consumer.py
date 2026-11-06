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
from typing import Any

from aiokafka import AIOKafkaConsumer, ConsumerRecord
from aiokafka.errors import KafkaError

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
    # 재시도로 재발행된 메시지는 message_id 가 같으므로 retry_count 를 포함해 멱등 키를 만든다
    dedupe_id = f"{env.message_id}:{msg.headers.get('retry_count', '0')}"
    if not await _acquire(dedupe_id):
        log.info("skip duplicate message %s", dedupe_id)
        return
    hb = asyncio.create_task(_heartbeat(dedupe_id))
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
    await get_redis().set(f"kafka:processed:{dedupe_id}", "done", ex=DONE_TTL_S)


POLL_INTERVAL_MS = 300_000  # 처리 중에도 poll 을 계속하므로 감사 길이와 무관하게 짧게 둔다


async def run_consumer(
    topics: list[str],
    group_id: str,
    handler: Handler,
    on_idle: Callable[[], None] | None = None,
    concurrency: int = 1,
) -> None:
    consumer = AIOKafkaConsumer(
        *topics,
        bootstrap_servers=get_settings().kafka_bootstrap,
        group_id=group_id,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        max_poll_interval_ms=POLL_INTERVAL_MS,
        session_timeout_ms=30000,
        heartbeat_interval_ms=10000,
    )
    await consumer.start()
    log.info("consumer started group=%s topics=%s concurrency=%d", group_id, topics, concurrency)
    try:
        await consume_loop(consumer, handler, concurrency, on_idle)
    finally:
        await consumer.stop()


async def consume_loop(
    consumer: Any,
    handler: Handler,
    concurrency: int = 1,
    on_idle: Callable[[], None] | None = None,
    stop: asyncio.Event | None = None,
) -> None:
    """파티션별 동시 처리 루프 (P3·B6).

    - 처리 중인 파티션은 pause 하고 poll 은 계속한다 → 처리 시간이 max_poll_interval 을 넘어도 리밸런스 없음 (P3)
    - 서로 다른 파티션의 메시지는 최대 `concurrency` 개까지 동시에 처리한다 (B6). 같은 파티션 안에서는 순서대로
    - 처리가 끝난 파티션만 그 offset 을 커밋하고 resume 한다 (at-least-once 유지)
    - 바로 처리하지 못하는 레코드(같은 파티션이 처리 중·동시 처리 상한)는 그 offset 으로 되감아 다음에 다시 받는다
    """
    inflight: dict[Any, tuple[asyncio.Task[None], ConsumerRecord]] = {}
    try:
        while not (stop is not None and stop.is_set() and not inflight):
            assigned = consumer.assignment()
            busy = [tp for tp in inflight if tp in assigned]
            if busy:
                consumer.pause(*busy)
            batch = await consumer.getmany(timeout_ms=500, max_records=max(1, concurrency))
            if on_idle:
                on_idle()
            for tp, recs in batch.items():
                if not recs:
                    continue
                full = len(inflight) >= concurrency or (stop is not None and stop.is_set())
                if tp in inflight or full:
                    consumer.seek(tp, recs[0].offset)
                    continue
                rec = recs[0]
                inflight[tp] = (asyncio.create_task(handle_record(rec, handler)), rec)
                consumer.pause(tp)
                if len(recs) > 1:
                    consumer.seek(tp, recs[1].offset)
            for tp, (task, rec) in list(inflight.items()):
                if not task.done():
                    continue
                del inflight[tp]
                await task  # handle_record 는 실패를 jobs.retry / jobs.dlq 로 보내고 예외를 올리지 않는다
                try:
                    await consumer.commit({tp: rec.offset + 1})
                except KafkaError as e:  # 리밸런스로 파티션을 잃은 경우: 새 소유자가 멱등 키로 처리 여부를 판단
                    log.warning("commit failed tp=%s offset=%s err=%s", tp, rec.offset, type(e).__name__)
                if tp in consumer.assignment():
                    consumer.resume(tp)
    finally:
        for task, _ in inflight.values():
            task.cancel()
