"""FastAPI 엔트리포인트 (prefix /api/v1). OpenAPI 문서 /docs."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import RequestResponseEndpoint

from api.errors import install_error_handlers
from api.local_mode import is_local, reject_reason
from api.routers import audits, auth, eval_runs, execution, knowledge, ops, results, targets
from core.kafka.producer import close_producer

API_PREFIX = "/api/v1"

# OWASP Secure Headers. JSON API 는 아무 리소스도 불러오지 않으므로 CSP 를 막아 둔다.
# /docs·/redoc 은 FastAPI 가 CDN 의 Swagger UI 를 쓰므로 CSP 를 붙이지 않는다.
SECURITY_HEADERS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
API_CSP = "default-src 'none'; frame-ancestors 'none'"
DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")

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


@app.middleware("http")
async def local_guard(request: Request, call_next: RequestResponseEndpoint) -> Response:
    if is_local():
        reason = reject_reason(request.method, request.headers)
        if reason:
            log.warning("local mode rejected %s %s: %s", request.method, request.url.path, reason)
            body = {"error": {"code": "FORBIDDEN", "message": f"local mode: {reason}", "details": {}}}
            return JSONResponse(body, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next: RequestResponseEndpoint) -> Response:
    response = await call_next(request)
    response.headers.update(SECURITY_HEADERS)
    if not request.url.path.startswith(DOCS_PATHS):
        response.headers["Content-Security-Policy"] = API_CSP
    return response


for r in (
    auth.router,
    targets.router,
    execution.router,
    audits.router,
    results.router,
    knowledge.router,
    ops.router,
    eval_runs.router,
):
    app.include_router(r, prefix=API_PREFIX)
