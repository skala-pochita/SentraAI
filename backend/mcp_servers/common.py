"""MCP 서버 공통 (명세 9.1·9.2).

- 전송: streamable-http, 경로 /mcp
- 인증: Authorization: Bearer <MCP_SERVICE_TOKEN> (미들웨어) + 호출 맥락 헤더 X-Actor-Id/X-Actor-Role/X-Audit-Id
- 응답: {"ok": true, "data", "meta"} / {"ok": false, "error": {code, message, retryable, retry_after_s?, details}}
- 변경 도구는 X-Actor-Role 로 권한을 서버에서 다시 검사한다.
"""

from __future__ import annotations

import contextvars
import functools
import hmac
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, ParamSpec

import uvicorn
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from core.errors import AppError, ErrorCode
from core.settings import get_settings
from harness.masking import mask_obj

log = logging.getLogger("mcp")

P = ParamSpec("P")
ALL_ROLES = ("ADMIN", "DEVELOPER", "AUDITOR")


@dataclass(frozen=True)
class CallContext:
    actor_id: str | None
    role: str
    audit_id: str | None
    request_id: str


# 테스트·로컬 호출에서 ctx 없이 쓰는 기본 맥락
default_call: contextvars.ContextVar[CallContext | None] = contextvars.ContextVar("default_call", default=None)
_current: contextvars.ContextVar[CallContext | None] = contextvars.ContextVar("current_call", default=None)


def current_call() -> CallContext:
    call = _current.get()
    if call is None:
        raise AppError(ErrorCode.INTERNAL, "no call context")
    return call


def _from_ctx(ctx: Context | None) -> CallContext:
    rid = uuid.uuid4().hex
    request = None
    if ctx is not None:
        try:
            request = ctx.request_context.request
        except (ValueError, AttributeError):
            request = None
    if isinstance(request, Request):
        h = request.headers
        return CallContext(h.get("x-actor-id"), (h.get("x-actor-role") or "").upper(), h.get("x-audit-id"), rid)
    fallback = default_call.get()
    if fallback is not None:
        return CallContext(fallback.actor_id, fallback.role, fallback.audit_id, rid)
    return CallContext(None, "", None, rid)


def ok(data: Any, started: float, request_id: str) -> dict[str, Any]:
    return {"ok": True, "data": mask_obj(data),
            "meta": {"request_id": request_id, "elapsed_ms": int((time.monotonic() - started) * 1000)}}


def fail(err: AppError, started: float, request_id: str) -> dict[str, Any]:
    return {"ok": False, "error": err.to_dict(),
            "meta": {"request_id": request_id, "elapsed_ms": int((time.monotonic() - started) * 1000)}}


def mcp_tool(
    server: FastMCP, roles: tuple[str, ...] = ALL_ROLES
) -> Callable[[Callable[P, Awaitable[Any]]], Callable[P, Awaitable[dict[str, Any]]]]:
    """도구 등록 + 공통 응답 envelope + 역할 검사. 도구 함수는 `ctx: Context | None = None` 인자를 가진다."""

    def deco(fn: Callable[P, Awaitable[Any]]) -> Callable[P, Awaitable[dict[str, Any]]]:
        @functools.wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> dict[str, Any]:
            started = time.monotonic()
            call = _from_ctx(kwargs.get("ctx"))  # type: ignore[arg-type]
            token = _current.set(call)
            try:
                if call.role not in roles:
                    raise AppError(ErrorCode.FORBIDDEN, f"{fn.__name__} requires role {'/'.join(roles)}",
                                   {"actor_role": call.role or None})
                data = await fn(*args, **kwargs)
                return ok(data, started, call.request_id)
            except AppError as e:
                log.info("tool %s failed: %s", fn.__name__, e.code)
                return fail(e, started, call.request_id)
            except Exception as e:  # noqa: BLE001 - 모든 예상 밖 오류를 INTERNAL 로 구조화 (로그 남김)
                log.exception("tool %s internal error", fn.__name__)
                return fail(AppError(ErrorCode.INTERNAL, type(e).__name__), started, call.request_id)
            finally:
                _current.reset(token)

        server.add_tool(wrapper, name=fn.__name__, description=fn.__doc__)
        return wrapper

    return deco


class ServiceTokenMiddleware:
    """/mcp 요청에 서비스 토큰 요구. /health 는 공개."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path", "").startswith("/mcp"):
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            auth = headers.get("authorization", "")
            expected = f"Bearer {get_settings().mcp_service_token}"
            if not hmac.compare_digest(auth.encode(), expected.encode()):
                res = JSONResponse({"ok": False, "error": AppError(ErrorCode.UNAUTHORIZED,
                                                                   "invalid service token").to_dict()}, 401)
                await res(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_server(name: str, port: int, instructions: str) -> FastMCP:
    server = FastMCP(
        name,
        instructions=instructions,
        host="0.0.0.0",  # noqa: S104 - 컨테이너 내부 바인딩
        port=port,
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    @server.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "server": name})

    return server


def run_server(server: FastMCP, port: int) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    app = ServiceTokenMiddleware(server.streamable_http_app())
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")  # noqa: S104
