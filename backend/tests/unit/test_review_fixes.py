"""코드 리뷰(workHistory/codeReview.md) 지적 사항 회귀 테스트 — 동시성·실패 경로.

A. 대상 락 획득·해제·연장의 원자성 (Lua)
B. Kafka 메시지 lease: 획득마다 새 소유자 토큰, 소유자 확인 heartbeat, lease 를 잃으면 처리 취소
3-1. 병렬 LLM 호출의 예산 초과 방지 (예약)
C. gather 실패 시 나머지 task 취소·정리
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from core.errors import AppError, ErrorCode

# ---- A. 대상 락 ----


async def test_release_does_not_delete_lock_taken_over_by_another_audit(fake_redis: Any) -> None:
    """A 의 락이 만료되고 B 가 새로 잡은 뒤 A 가 해제를 시도해도 B 의 락은 남는다 (TOCTOU)."""
    from core.redis.locks import acquire_target_lock, release_target_lock

    await acquire_target_lock("t1", "audit-A")
    await fake_redis.delete("lock:audit:target:t1")  # A 의 락 만료
    await acquire_target_lock("t1", "audit-B")
    assert await release_target_lock("t1", "audit-A") is False
    assert await fake_redis.get("lock:audit:target:t1") == "audit-B"
    assert await release_target_lock("t1", "audit-B") is True
    assert await fake_redis.get("lock:audit:target:t1") is None


async def test_acquire_conflict_and_same_audit_reacquire(fake_redis: Any) -> None:
    from core.redis.locks import LOCK_TTL_S, acquire_target_lock

    await acquire_target_lock("t1", "audit-A")
    await fake_redis.expire("lock:audit:target:t1", 5)
    await acquire_target_lock("t1", "audit-A")  # 재전달·재개: 같은 감사는 통과 + TTL 갱신
    assert await fake_redis.ttl("lock:audit:target:t1") > LOCK_TTL_S - 5
    with pytest.raises(AppError) as ei:
        await acquire_target_lock("t1", "audit-B")
    assert ei.value.code == ErrorCode.CONFLICT and ei.value.details["holder"] == "audit-A"


async def test_extend_only_own_locks_in_one_call(fake_redis: Any) -> None:
    from core.redis.locks import extend_target_locks

    await fake_redis.set("lock:audit:target:a", "audit-A", ex=5)
    await fake_redis.set("lock:audit:target:b", "audit-A", ex=5)
    await fake_redis.set("lock:audit:target:c", "audit-B", ex=5)
    assert await extend_target_locks(["a", "b", "c", "gone"], "audit-A", ttl_s=900) == 2
    assert await fake_redis.ttl("lock:audit:target:a") > 800
    assert await fake_redis.ttl("lock:audit:target:c") <= 5
    assert await fake_redis.get("lock:audit:target:gone") is None  # 없는 락을 새로 만들지 않는다
    assert await extend_target_locks([], "audit-A") == 0


async def test_lock_heartbeat_warns_when_lock_lost(fake_redis: Any, caplog: pytest.LogCaptureFixture) -> None:
    from core.redis.locks import keep_locks_alive

    await fake_redis.set("lock:audit:target:a", "audit-B", ex=60)  # 이미 다른 감사가 잡음
    caplog.set_level(logging.WARNING, logger="locks")
    task = asyncio.create_task(keep_locks_alive(["a"], "audit-A", interval_s=0.01))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert any("lock lost" in r.message for r in caplog.records)
    assert await fake_redis.get("lock:audit:target:a") == "audit-B"


# ---- B. Kafka lease ----


@pytest.fixture
def kafka(monkeypatch: pytest.MonkeyPatch, fake_redis: Any) -> Any:
    from core.kafka import consumer as kc

    sent: list[str] = []

    async def fake_publish(topic: str, key: str, value: Any, headers: Any = None) -> None:
        sent.append(topic)

    monkeypatch.setattr(kc, "publish", fake_publish)
    monkeypatch.setattr(kc, "HEARTBEAT_S", 0.02)
    monkeypatch.setattr(kc, "ACQUIRE_POLL_S", 0.01)
    return SimpleNamespace(kc=kc, sent=sent, redis=fake_redis)


def _record() -> tuple[Any, str]:
    from core.kafka.envelope import Envelope, headers_to_kafka

    env = Envelope(type="audit.requested", payload={"audit_id": str(uuid.uuid4())})
    rec = SimpleNamespace(topic="audit.requested", key=b"k", value=env.encode(), offset=1, headers=headers_to_kafka({}))
    return rec, f"kafka:processed:{env.message_id}:0"


async def test_lease_token_is_unique_per_acquisition_even_in_same_process(kafka: Any) -> None:
    """같은 워커 프로세스 안의 두 번째 획득도 '내 것'으로 통과하지 않고 lease 해제를 기다린다 (B6 동시 처리)."""
    t1 = await kafka.kc._acquire("m1")
    assert t1 is not None and t1.startswith(f"processing:{kafka.kc.WORKER_ID}:")
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(kafka.kc._acquire("m1"), timeout=0.1)
    await kafka.redis.delete("kafka:processed:m1")  # lease 만료
    t2 = await kafka.kc._acquire("m1")
    assert t2 is not None and t2 != t1
    await kafka.redis.set("kafka:processed:m1", "done")
    assert await kafka.kc._acquire("m1") is None


async def test_heartbeat_renews_only_own_lease(kafka: Any) -> None:
    rec, key = _record()
    seen: list[int] = []

    async def handler(msg: Any) -> None:
        await kafka.redis.expire(key, 5)
        await asyncio.sleep(0.08)  # heartbeat 가 몇 번 돈다
        seen.append(await kafka.redis.ttl(key))

    await kafka.kc.handle_record(rec, handler)
    assert seen[0] > 5  # 내 lease 는 LEASE_S 로 연장됨
    assert await kafka.redis.get(key) == "done"
    assert kafka.sent == []


async def test_lost_lease_aborts_processing_and_keeps_new_owner(kafka: Any) -> None:
    """lease 를 잃은 워커는 처리를 취소하고, 새 소유자의 lease 를 덮어쓰거나 done 으로 바꾸지 않는다."""
    rec, key = _record()
    finished: list[bool] = []

    async def handler(msg: Any) -> None:
        await kafka.redis.set(key, "processing:other-worker:abc", ex=60)  # 만료 후 다른 워커가 획득
        await asyncio.sleep(1)
        finished.append(True)

    await asyncio.wait_for(kafka.kc.handle_record(rec, handler), timeout=0.5)
    assert finished == []  # 끝까지 처리하지 않음 (중복 처리 방지)
    assert await kafka.redis.get(key) == "processing:other-worker:abc"
    assert kafka.sent == []  # 실패로 라우팅하지도 않는다 (새 소유자가 처리)


async def test_outer_cancel_propagates_and_stops_handler(kafka: Any) -> None:
    rec, _ = _record()
    cancelled: list[bool] = []

    async def handler(msg: Any) -> None:
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    task = asyncio.create_task(kafka.kc.handle_record(rec, handler))
    await asyncio.sleep(0.03)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled == [True]


async def test_redis_error_in_heartbeat_does_not_crash_handler(kafka: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from redis.exceptions import ConnectionError as RedisConnError

    rec, key = _record()
    real_eval = kafka.redis.eval
    calls = {"n": 0}

    async def flaky_eval(script: str, *a: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RedisConnError("blip")
        return await real_eval(script, *a)

    monkeypatch.setattr(kafka.redis, "eval", flaky_eval)

    async def handler(msg: Any) -> None:
        await asyncio.sleep(0.07)

    await kafka.kc.handle_record(rec, handler)
    assert await kafka.redis.get(key) == "done" and kafka.sent == []


# ---- 3-1. 병렬 LLM 호출 예산 ----


class SlowModel:
    """ainvoke 만 흉내 낸다. 호출마다 고정 토큰을 쓴다."""

    def __init__(self, tokens: int, delay: float = 0.03, fail: bool = False) -> None:
        self.tokens, self.delay, self.fail, self.calls = tokens, delay, fail, 0

    async def ainvoke(self, messages: Any) -> AIMessage:
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.fail:
            raise AppError(ErrorCode.UPSTREAM_TIMEOUT, "boom")
        half = self.tokens // 2
        return AIMessage(
            content="{}",
            usage_metadata={"input_tokens": half, "output_tokens": half, "total_tokens": self.tokens},
        )


async def _call(model: Any, budget: Any) -> str:
    from harness.llm import invoke_messages

    try:
        await invoke_messages(
            model, [HumanMessage("x" * 40)], node="t", model_name="none", prompt_ref=None, budget=budget, usage=[]
        )
        return "ok"
    except AppError as e:
        return str(e.code)


async def test_parallel_llm_calls_do_not_overshoot_budget() -> None:
    """예전: 8개가 동시에 '아직 예산 남음'을 확인 → 8 × 2500 = 20000 토큰 (상한 10000의 2배)."""
    from harness.budget import Budget

    b = Budget(max_tokens=10_000, max_cost_usd=0, reserve_output_tokens=2500)
    model = SlowModel(tokens=2500)
    out = await asyncio.gather(*(_call(model, b) for _ in range(8)))
    assert out.count("ok") == 3 and model.calls == 3  # 예약 2510 × 3 = 7530, 4번째는 10040 > 10000
    assert out.count(str(ErrorCode.BUDGET_EXCEEDED)) == 5
    assert b.used_tokens <= b.max_tokens
    assert b.reserved_tokens == 0 and b.reserved_usd == 0


async def test_failed_llm_call_releases_reservation() -> None:
    from harness.budget import Budget

    b = Budget(max_tokens=10_000, max_cost_usd=0, reserve_output_tokens=2000)
    assert await _call(SlowModel(tokens=0, fail=True), b) == str(ErrorCode.UPSTREAM_TIMEOUT)
    assert b.reserved_tokens == 0 and b.used_tokens == 0
    assert await _call(SlowModel(tokens=100), b) == "ok"  # 예약이 남아 막히지 않는다


def test_budget_reserve_is_check_and_reserve() -> None:
    from harness.budget import Budget

    b = Budget(max_tokens=1000, max_cost_usd=1.0)
    assert b.reserve(600, 0.1)
    assert not b.reserve(600, 0.1)  # 진행 중 예약까지 합쳐서 판단
    assert not b.reserve(100, 0.95)  # 비용 상한도
    b.release(600, 0.1)
    assert b.reserve(600, 0.1)


# ---- C. gather 실패 경로 ----


async def test_gather_or_cancel_cancels_and_awaits_siblings() -> None:
    from core.aio import gather_or_cancel

    state = {"cancelled": 0, "finished": 0}

    async def slow() -> int:
        try:
            await asyncio.sleep(1)
            state["finished"] += 1
            return 1
        except asyncio.CancelledError:
            state["cancelled"] += 1
            raise

    async def bad() -> int:
        await asyncio.sleep(0.01)
        raise AppError(ErrorCode.UPSTREAM_ERROR, "x")

    with pytest.raises(AppError):  # ExceptionGroup 으로 감싸지 않는다
        await gather_or_cancel(slow(), bad(), slow())
    assert state == {"cancelled": 2, "finished": 0}  # 예외가 올라온 시점에 이미 정리 완료


async def test_gather_or_cancel_keeps_order() -> None:
    from core.aio import gather_or_cancel

    async def v(x: int, d: float) -> int:
        await asyncio.sleep(d)
        return x

    assert await gather_or_cancel(v(1, 0.03), v(2, 0.0), v(3, 0.01)) == [1, 2, 3]
    assert await gather_or_cancel() == []


async def test_probe_attempt_failure_does_not_leave_orphans_on_closed_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """run_injection_probe: 시도 하나가 실패하면 나머지 시도는 닫힌 httpx 클라이언트를 쓰기 전에 취소된다."""
    from mcp_servers.business.tools import execute as ex

    used_after_close: list[int] = []

    async def fake_converse(client: Any, t: Any, url: str, audit_id: str, probe: Any) -> dict[str, Any]:
        n = len(calls)
        calls.append(n)
        if n == 0:
            await asyncio.sleep(0.01)
            raise AppError(ErrorCode.UPSTREAM_ERROR, "target down")
        await asyncio.sleep(0.05)
        if client.is_closed:
            used_after_close.append(n)
        return {}

    calls: list[int] = []
    target = SimpleNamespace(id="t", ref="http://x", config={"source": "upload"})

    async def fake_get_target(*a: Any) -> Any:
        return target

    async def noop(*a: Any) -> Any:
        return SimpleNamespace(id="ps")

    monkeypatch.setattr(ex, "get_target", fake_get_target)
    monkeypatch.setattr(ex, "_sandbox_access", noop)
    monkeypatch.setattr(ex, "_probe_set_for", noop)
    monkeypatch.setattr(ex, "_resolve_probe", lambda *a: SimpleNamespace(id="p", category="c", channel="chat"))
    monkeypatch.setattr(ex, "_converse", fake_converse)
    monkeypatch.setattr(ex, "current_call", lambda: SimpleNamespace(audit_id="a"))
    monkeypatch.setattr(ex, "attempt_concurrency", lambda: 3)

    fn = ex.run_injection_probe.__wrapped__  # type: ignore[attr-defined]  # 응답 envelope 없이 원래 함수
    with pytest.raises(AppError):
        await fn("t", probe_id="p", attempts=3)
    await asyncio.sleep(0.1)  # 고아 task 가 있었다면 이 사이에 닫힌 클라이언트를 쓴다
    assert used_after_close == []
