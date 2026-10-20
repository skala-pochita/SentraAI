"""eval-worker (cg-eval): eval.requested 소비 → A/B/C 비교 실험 실행 (명세 11·17장).

명세의 eval.tasks 분할 대신 한 워커가 (config × repeat) 를 순서대로 실행한다 (DECISIONS #23):
B 구성이 target-agent·대상 락을 쓰므로 동시 실행하면 서로 간섭한다.
"""

from __future__ import annotations

import logging

from core.kafka.consumer import Message, run_consumer
from harness.eval.runner import run_eval

log = logging.getLogger("worker.eval")


async def handle(msg: Message) -> None:
    p = msg.envelope.payload
    await run_eval(list(p["configs"]), int(p.get("repeat", 1)), eval_run_id=p["eval_run_id"])


async def run_eval_worker() -> None:
    await run_consumer(["eval.requested"], "cg-eval", handle)
