"""워커 엔트리포인트: python -m workers.main --role audit|eval|retry."""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from pathlib import Path

from core.kafka.consumer import run_consumer
from core.kafka.producer import close_producer
from harness.observability import configure_langsmith

log = logging.getLogger("worker")

HEARTBEAT_DIR = Path("/tmp")  # noqa: S108 - 컨테이너 healthcheck 용


def heartbeat(role: str) -> None:
    (HEARTBEAT_DIR / f"worker-{role}.heartbeat").write_text(str(time.time()))


async def _heartbeat_loop(role: str) -> None:
    while True:
        heartbeat(role)
        await asyncio.sleep(10)


async def run_audit() -> None:
    from workers.audit import AuditRunner, make_checkpointer, webhook_to_resume

    async with await make_checkpointer() as saver:
        await saver.asetup()
        runner = AuditRunner(saver)
        from workers.cloud import CloudRunner

        cloud = CloudRunner(saver)

        async def on_audit(msg):  # type: ignore[no-untyped-def]
            # Agent·Cloud 감사는 payload 에 task_id 가 있다 (같은 토픽·컨슈머 그룹 사용)
            await (cloud if "task_id" in msg.envelope.payload else runner).handle(msg)

        async def on_webhook(msg):  # type: ignore[no-untyped-def]
            await webhook_to_resume(msg)

        await asyncio.gather(
            run_consumer(["audit.requested", "audit.resume"], "cg-audit", on_audit),
            run_consumer(["github.webhook"], "cg-webhook", on_webhook),
        )


async def run_eval() -> None:
    from workers.eval import run_eval_worker

    await run_eval_worker()


async def run_retry() -> None:
    from workers.retry import run_retry_worker

    await run_retry_worker(lambda: heartbeat("retry"))


async def run(role: str) -> None:
    configure_langsmith()
    log.info("worker started role=%s", role)
    hb = asyncio.create_task(_heartbeat_loop(role))
    try:
        while True:
            try:
                await {"audit": run_audit, "eval": run_eval, "retry": run_retry}[role]()
            except (OSError, ConnectionError) as e:  # 브로커·Redis 재연결
                log.warning("worker %s connection error: %s; restarting in 5s", role, type(e).__name__)
                await asyncio.sleep(5)
    finally:
        hb.cancel()
        await close_producer()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", choices=["audit", "eval", "retry"], required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run(args.role))


if __name__ == "__main__":
    main()
