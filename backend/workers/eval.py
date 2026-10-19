"""eval-worker (cg-eval, cg-eval-task): A/B/C 비교 실험 (Phase 8 에서 구현)."""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("worker.eval")


async def run_eval_worker() -> None:
    log.info("eval worker idle (Phase 8)")
    while True:
        await asyncio.sleep(3600)
