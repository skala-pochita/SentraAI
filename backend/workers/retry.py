"""retry-worker (cg-retry): jobs.retry 소비 → next_attempt_at 까지 대기(파티션 pause) → 원래 토픽 재발행.
api (cg-dlq): jobs.dlq → dlq_messages 적재 (명세 11.3)."""

from __future__ import annotations

import asyncio
import json
import logging
import time

from aiokafka import AIOKafkaConsumer

from core.db.models import DlqMessage
from core.db.session import session_scope
from core.kafka.envelope import headers_from_kafka
from core.kafka.producer import publish
from core.settings import get_settings

log = logging.getLogger("worker.retry")


async def run_retry_worker(heartbeat: object = None) -> None:
    consumer = AIOKafkaConsumer(
        "jobs.retry",
        bootstrap_servers=get_settings().kafka_bootstrap,
        group_id="cg-retry",
        enable_auto_commit=False,
        auto_offset_reset="earliest",
    )
    await consumer.start()
    log.info("retry worker started")
    try:
        while True:
            if callable(heartbeat):
                heartbeat()
            batch = await consumer.getmany(timeout_ms=1000, max_records=1)
            for tp, records in batch.items():
                for rec in records:
                    h = headers_from_kafka(rec.headers)
                    wait_ms = int(h.get("next_attempt_at", "0")) - int(time.time() * 1000)
                    if wait_ms > 0:
                        consumer.pause(tp)  # 해당 파티션 pause → 남은 시간 sleep → resume
                        log.info("retry wait %.1fs for key=%s", wait_ms / 1000, rec.key)
                        end = time.monotonic() + wait_ms / 1000
                        while time.monotonic() < end:
                            if callable(heartbeat):
                                heartbeat()
                            await asyncio.sleep(min(5.0, end - time.monotonic()))
                        consumer.resume(tp)
                    original = h.get("original_topic", "audit.requested")
                    await publish(original, (rec.key or b"").decode(), rec.value or b"", h)
                    await consumer.commit()
                    log.info("republished to %s retry_count=%s", original, h.get("retry_count"))
    finally:
        await consumer.stop()


async def store_dlq(topic: str, key: bytes | None, value: bytes | None, headers: list[tuple[str, bytes]]) -> None:
    h = headers_from_kafka(headers)
    try:
        msg = json.loads(value or b"{}")
    except json.JSONDecodeError:
        msg = {"raw": (value or b"").decode(errors="replace")[:2000]}
    async with session_scope() as db:
        db.add(
            DlqMessage(
                original_topic=h.get("original_topic", topic),
                message_key=(key or b"").decode(),
                message=msg,
                headers=h,
                error_code=h.get("last_error_code"),
                error_message=h.get("last_error_message"),
            )
        )


async def run_dlq_consumer() -> None:
    consumer = AIOKafkaConsumer(
        "jobs.dlq",
        bootstrap_servers=get_settings().kafka_bootstrap,
        group_id="cg-dlq",
        enable_auto_commit=False,
        auto_offset_reset="earliest",
    )
    await consumer.start()
    try:
        async for rec in consumer:
            await store_dlq(rec.topic, rec.key, rec.value, list(rec.headers or []))
            await consumer.commit()
            log.warning("DLQ message stored key=%s", rec.key)
    finally:
        await consumer.stop()
