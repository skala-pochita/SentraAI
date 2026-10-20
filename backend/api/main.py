"""FastAPI 엔트리포인트 (prefix /api/v1). OpenAPI 문서 /docs."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.errors import install_error_handlers
from api.routers import audits, auth, eval_runs, execution, knowledge, ops, quality, targets
from core.kafka.producer import close_producer

API_PREFIX = "/api/v1"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    tasks: list[asyncio.Task[None]] = []
    if getattr(app.state, "background_consumers", True):
        from workers.retry import run_dlq_consumer

        async def dlq_loop() -> None:
            while True:
                try:
                    await run_dlq_consumer()
                except (OSError, ConnectionError) as e:
                    log.warning("dlq consumer error %s; retry in 5s", type(e).__name__)
                except Exception:  # noqa: BLE001 - 백그라운드 소비자는 죽지 않고 재시작 (로그 남김)
                    log.exception("dlq consumer crashed; restart in 5s")
                await asyncio.sleep(5)

        tasks.append(asyncio.create_task(dlq_loop()))
    yield
    for t in tasks:
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t
    await close_producer()


app = FastAPI(title="AI Security Auditor API", version="0.1.0", lifespan=lifespan)
install_error_handlers(app)

for r in (
    auth.router,
    targets.router,
    execution.router,
    audits.router,
    knowledge.router,
    ops.router,
    eval_runs.router,
    quality.router,
):
    app.include_router(r, prefix=API_PREFIX)
