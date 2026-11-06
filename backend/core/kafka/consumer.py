"""Kafka consumer 기반 (명세 11.3).

- enable_auto_commit=False, 처리 완료 후 수동 커밋 (at-least-once)
- 멱등: kafka:processed:{message_id} — 처리 중엔 lease("processing:<worker>:<token>", 60s, 하트비트 갱신),
  완료 후 "done" 24h. 워커가 처리 중 죽으면 lease 만료 후 재전달 메시지를 이어받는다 (DECISIONS #6).
  하트비트·완료 표시는 소유자 토큰이 일치할 때만 하고, lease 를 잃은 워커는 처리를 취소한다.
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
from redis.exceptions import RedisError

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


# lease 값: processing:{worker}:{획득마다 새 토큰}. 같은 프로세스 안의 동시 처리(B6)도 서로 다른 소유자로 구분한다.
# 소유자 확인과 변경은 Lua 한 번으로 처리한다 (2026-11-06 리뷰 B)
_RENEW = """
if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end
"""
_FINISH = """
if redis.call('get', KEYS[1]) == ARGV[1] then redis.call('set', KEYS[1], 'done', 'EX', ARGV[2]) return 1 else return 0 end
"""
HEARTBEAT_S = LEASE_S / 3
ACQUIRE_POLL_S = 5.0


def _lease_key(message_id: str) -> str:
    return f"kafka:processed:{message_id}"


async def _acquire(message_id: str) -> str | None:
    """lease 토큰 = 처리 시작, None = 이미 완료(skip). 다른 소유자가 처리 중이면 lease 만료(또는 완료)까지 대기."""
    r = get_redis()
    token = f"processing:{WORKER_ID}:{uuid.uuid4().hex[:12]}"
    while True:
        if await r.set(_lease_key(message_id), token, nx=True, ex=LEASE_S):
            return token
        if await r.get(_lease_key(message_id)) == "done":
            return None
        await asyncio.sleep(ACQUIRE_POLL_S)


async def _heartbeat(message_id: str, token: str, work: asyncio.Task[None], lost: asyncio.Event) -> None:
    """내 lease 일 때만 TTL 을 늘린다. lease 를 잃었으면(만료 후 다른 소유자가 획득) 처리를 취소한다
    → 같은 메시지를 두 워커가 끝까지 처리하지 않는다. Redis 오류는 경고 후 다음 주기에 다시 시도."""
    r = get_redis()
    while True:
        await asyncio.sleep(HEARTBEAT_S)
        try:
            ok = await r.eval(_RENEW, 1, _lease_key(message_id), token, LEASE_S)
        except (RedisError, OSError) as e:
            log.warning("lease heartbeat failed %s err=%s", message_id, type(e).__name__)
            continue
        if not ok:
            log.warning("lease lost %s → abort processing (new owner continues)", message_id)
            lost.set()
            work.cancel()
            return


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
    token = await _acquire(dedupe_id)
    if token is None:
        log.info("skip duplicate message %s", dedupe_id)
        return

    async def run() -> None:
        await handler(msg)

    work = asyncio.create_task(run())
    lost = asyncio.Event()
    hb = asyncio.create_task(_heartbeat(dedupe_id, token, work, lost))
    try:
        await work
    except asyncio.CancelledError:
        cur = asyncio.current_task()
        if not lost.is_set() or (cur is not None and cur.cancelling()):
            raise  # 종료·리밸런스 등 바깥에서 취소: 그대로 전파 (lease 는 만료 후 다른 워커가 이어받음)
        return  # lease 를 잃음: 새 소유자가 처리한다 → 실패 라우팅·done 표시를 하지 않는다
    except AppError as e:
        await route_failure(msg, e)
    except Exception as e:  # noqa: BLE001 - 예상 밖 오류는 INTERNAL 로 DLQ (로그 남김)
        log.exception("handler crashed for %s", env.message_id)
        await route_failure(msg, AppError(ErrorCode.INTERNAL, f"{type(e).__name__}: {e}"[:300]))
    finally:
        work.cancel()
        hb.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await hb
    if not await get_redis().eval(_FINISH, 1, _lease_key(dedupe_id), token, DONE_TTL_S):
        log.warning("lease lost before completion %s → not marking done", dedupe_id)


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
