"""Kafka producer (싱글턴)."""

from __future__ import annotations

import asyncio
import logging

from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaError

from core.errors import AppError, ErrorCode
from core.kafka.envelope import Envelope, Headers, headers_to_kafka
from core.settings import get_settings

log = logging.getLogger("kafka.producer")

_producer: AIOKafkaProducer | None = None
_lock = asyncio.Lock()
SEND_TIMEOUT_S = 10


async def get_producer() -> AIOKafkaProducer:
    global _producer
    async with _lock:
        if _producer is None:
            p = AIOKafkaProducer(
                bootstrap_servers=get_settings().kafka_bootstrap,
                acks="all",
                enable_idempotence=True,
                request_timeout_ms=10000,
            )
            try:
                await asyncio.wait_for(p.start(), timeout=SEND_TIMEOUT_S)
            except (KafkaError, TimeoutError, OSError) as e:
                await p.stop()
                raise AppError(ErrorCode.UPSTREAM_ERROR, f"kafka unavailable: {type(e).__name__}") from e
            _producer = p
        return _producer


async def publish(topic: str, key: str, env: Envelope | bytes, headers: Headers | None = None) -> None:
    producer = await get_producer()
    value = env.encode() if isinstance(env, Envelope) else env
    try:
        await asyncio.wait_for(
            producer.send_and_wait(topic, value=value, key=key.encode(), headers=headers_to_kafka(headers or {})),
            timeout=SEND_TIMEOUT_S,
        )
    except (KafkaError, TimeoutError) as e:
        raise AppError(ErrorCode.UPSTREAM_ERROR, f"kafka publish failed: {type(e).__name__}") from e
    log.info("published topic=%s key=%s", topic, key)


async def close_producer() -> None:
    global _producer
    if _producer is not None:
        await _producer.stop()
        _producer = None
