"""워커 → MCP 서버 호출 래퍼 (명세 8.3 오류 표 아래 설명, 9.1).

- 서비스 토큰 + 맥락 헤더(X-Actor-Id/X-Actor-Role/X-Audit-Id)
- H3 도구 게이트(노드별 allowlist, 역할) → 호출 → {ok,data|error} 해석
- retryable 오류는 같은 노드 안에서 0.5s, 2s 재시도 후 RetryableNodeError (Kafka 재시도로)
- H6 tool span 기록

HttpGateway 는 streamable-http 로, InProcessGateway 는 같은 프로세스의 FastMCP 인스턴스를 직접 호출한다(테스트·평가용).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

from langchain_mcp_adapters.client import MultiServerMCPClient
from mcp.server.fastmcp import FastMCP

from core.errors import AppError, ErrorCode
from core.settings import get_settings, load_config
from harness.observability import span
from harness.reliability import with_retries
from harness.tools import ToolGate, server_of

log = logging.getLogger("mcp_client")


@dataclass(frozen=True)
class CallCtx:
    node: str
    actor_role: str
    actor_id: str | None = None
    audit_id: str | None = None


def parse_result(text: str) -> Any:
    try:
        body = json.loads(text)
    except json.JSONDecodeError as e:
        raise AppError(ErrorCode.UPSTREAM_ERROR, "invalid MCP response (not JSON)") from e
    if not isinstance(body, dict) or "ok" not in body:
        raise AppError(ErrorCode.UPSTREAM_ERROR, "invalid MCP response envelope")
    if body["ok"]:
        return body.get("data")
    err = body.get("error", {})
    try:
        code = ErrorCode(err.get("code", "INTERNAL"))
    except ValueError:
        code = ErrorCode.INTERNAL
    raise AppError(code, err.get("message", "MCP tool error"), err.get("details"), err.get("retry_after_s"))


def _first_text(content: Any) -> str:
    for c in content or []:
        if getattr(c, "type", None) == "text":
            return str(c.text)
    raise AppError(ErrorCode.UPSTREAM_ERROR, "empty MCP response")


class Transport(Protocol):
    async def raw_call(self, server: str, tool: str, args: dict[str, Any], ctx: CallCtx, timeout: float) -> str: ...


POOL_HTTP_TIMEOUT_S = (
    1300.0  # 재사용 세션의 HTTP 상한 (가장 긴 도구 prepare_agent_sandbox 1200s 이상). 호출별 상한은 wait_for
)


class _PooledSession:
    """MCP 세션 1개를 전용 task 가 열고 닫는다 (anyio cancel scope 는 연 task 에서 닫아야 한다).
    다른 task 들은 열린 세션으로 call_tool 을 동시에 호출할 수 있다 (요청 id 로 다중화)."""

    def __init__(self, client: MultiServerMCPClient, server: str) -> None:
        self.client = client
        self.server = server
        self.session: Any = None
        self.error: BaseException | None = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    async def start(self, timeout: float) -> None:
        self._task = asyncio.create_task(self._run())
        await asyncio.wait_for(self._ready.wait(), timeout=timeout)
        if self.error is not None:
            raise self.error

    async def _run(self) -> None:
        try:
            async with self.client.session(self.server) as session:
                # call_tool 은 출력 스키마 캐시에 없는 도구면 매번 list_tools 를 부른다 → 열 때 한 번 채워 둔다
                await session.list_tools()
                self.session = session
                self._ready.set()
                await self._stop.wait()
        except (OSError, ExceptionGroup, RuntimeError) as e:  # 연결 실패·서버 종료: 호출자에게 구조화 오류로 전달
            self.error = e
            log.info("pooled MCP session %s closed: %s", self.server, type(e).__name__)
        finally:
            self.session = None
            self._ready.set()

    @property
    def alive(self) -> bool:
        return self.session is not None and self._task is not None and not self._task.done()

    async def close(self) -> None:
        self._stop.set()
        if self._task is not None:
            with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                await asyncio.wait_for(self._task, timeout=5)


class HttpTransport:
    """streamable-http 전송. reuse=True 면 (서버, 맥락 헤더) 별로 세션을 열어 두고 재사용한다 (B2).

    감사 1건의 호출은 맥락 헤더(X-Actor-*, X-Audit-Id)가 같으므로 세션 1개로 모인다.
    Gateway 는 감사마다 새로 만들고 감사가 끝나면 aclose() 로 세션을 닫는다.
    """

    def __init__(self, reuse: bool = True) -> None:
        self.reuse = reuse
        self._pool: dict[tuple[str, tuple[tuple[str, str], ...]], _PooledSession] = {}
        self._lock = asyncio.Lock()

    def _url(self, server: str) -> str:
        s = get_settings()
        return s.business_mcp_url if server == "business" else s.rag_mcp_url

    def _headers(self, ctx: CallCtx) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {get_settings().mcp_service_token}", "X-Actor-Role": ctx.actor_role}
        if ctx.actor_id:
            headers["X-Actor-Id"] = ctx.actor_id
        if ctx.audit_id:
            headers["X-Audit-Id"] = ctx.audit_id
        return headers

    def _client(self, server: str, headers: dict[str, str], timeout: float) -> MultiServerMCPClient:
        # 명세 9.1: Agent 는 langchain-mcp-adapters MultiServerMCPClient 로 두 MCP 서버에 연결한다.
        return MultiServerMCPClient(
            {
                server: {
                    "transport": "streamable_http",
                    "url": self._url(server),
                    "headers": headers,
                    "timeout": timeout,
                    "sse_read_timeout": timeout,
                }
            }
        )

    async def _session(self, server: str, headers: dict[str, str], timeout: float) -> Any:
        key = (server, tuple(sorted(headers.items())))
        async with self._lock:
            pooled = self._pool.get(key)
            if pooled is None or not pooled.alive:
                if pooled is not None:
                    await pooled.close()
                pooled = _PooledSession(self._client(server, headers, POOL_HTTP_TIMEOUT_S), server)
                self._pool[key] = pooled
                try:
                    await pooled.start(timeout)
                except BaseException:
                    self._pool.pop(key, None)
                    raise
            return pooled

    async def raw_call(self, server: str, tool: str, args: dict[str, Any], ctx: CallCtx, timeout: float) -> str:
        headers = self._headers(ctx)

        async def _once() -> str:
            client = self._client(server, headers, timeout)
            async with client.session(server) as session:
                res = await session.call_tool(tool, args)
                return _first_text(res.content)

        async def _pooled() -> str:
            pooled = await self._session(server, headers, timeout)
            try:
                res = await pooled.session.call_tool(tool, args)
            except (OSError, ExceptionGroup, RuntimeError) as e:
                await self._drop(pooled)  # 끊긴 세션은 버리고 다음 재시도(with_retries)에서 새로 연다
                raise AppError(ErrorCode.UPSTREAM_ERROR, f"MCP {server} session broken: {type(e).__name__}") from e
            return _first_text(res.content)

        try:
            return await asyncio.wait_for(_pooled() if self.reuse else _once(), timeout=timeout)
        except TimeoutError as e:
            raise AppError(ErrorCode.UPSTREAM_TIMEOUT, f"MCP {server}.{tool} timed out") from e
        except AppError:
            raise
        except (OSError, ExceptionGroup) as e:  # 연결 실패 등 전송 오류
            raise AppError(ErrorCode.UPSTREAM_ERROR, f"MCP {server} unreachable: {type(e).__name__}") from e

    async def _drop(self, pooled: _PooledSession) -> None:
        async with self._lock:
            for k, v in list(self._pool.items()):
                if v is pooled:
                    self._pool.pop(k)
        await pooled.close()

    async def aclose(self) -> None:
        async with self._lock:
            pooled, self._pool = list(self._pool.values()), {}
        for p in pooled:
            await p.close()


class InProcessTransport:
    def __init__(self, servers: dict[str, FastMCP]) -> None:
        self.servers = servers

    async def raw_call(self, server: str, tool: str, args: dict[str, Any], ctx: CallCtx, timeout: float) -> str:
        from mcp_servers.common import CallContext, default_call

        token = default_call.set(CallContext(ctx.actor_id, ctx.actor_role, ctx.audit_id, "inproc"))
        try:
            content = await asyncio.wait_for(self.servers[server].call_tool(tool, args), timeout=timeout)
        except TimeoutError as e:
            raise AppError(ErrorCode.UPSTREAM_TIMEOUT, f"MCP {server}.{tool} timed out") from e
        finally:
            default_call.reset(token)
        return _first_text(content)


@dataclass
class Gateway:
    transport: Transport
    gate: ToolGate = field(default_factory=ToolGate)
    log: list[dict[str, Any]] = field(default_factory=list)

    def _timeout(self, tool: str, args: dict[str, Any]) -> float:
        base = float(load_config("budget").get("mcp", {}).get("call_timeout_s", 30))
        if tool == "prepare_agent_sandbox":
            return 1200.0
        if tool == "run_injection_probe":
            return max(base, 330.0 * int(args.get("attempts", 3)))
        if tool in ("run_static_scan", "run_agent_rule_check"):
            return max(base, 120.0)
        if tool == "run_external_scan":  # 외부 도구별 180s, 동시 실행
            return max(base, 240.0)
        return base

    async def call(self, tool: str, args: dict[str, Any], ctx: CallCtx) -> Any:
        server = server_of(tool)
        self.gate.check(ctx.node, tool, ctx.actor_role)
        clean = {k: v for k, v in args.items() if v is not None}
        timeout = self._timeout(tool, clean)

        async def once() -> Any:
            async with span("tool", f"{server}.{tool}", node=ctx.node):
                return parse_result(await self.transport.raw_call(server, tool, clean, ctx, timeout))

        result = await with_retries(once, dep=f"mcp:{server}")
        self.log.append({"node": ctx.node, "tool": tool})
        return result


async def close_gateway(gw: Gateway) -> None:
    """감사가 끝나면 열어 둔 MCP 세션을 닫는다 (재사용 전송만 해당)."""
    closer = getattr(gw.transport, "aclose", None)
    if closer is not None:
        await closer()


def http_gateway() -> Gateway:
    """API 의 1회성 호출용: 호출마다 세션을 연다."""
    return Gateway(HttpTransport(reuse=False))


def audit_gateway() -> Gateway:
    """감사 실행용: 세션을 재사용한다 (B2). 감사가 끝나면 close_gateway() 로 닫는다."""
    return Gateway(HttpTransport(reuse=True))


def inprocess_gateway() -> Gateway:
    from mcp_servers.business import tools as _bt  # noqa: F401 - 도구 등록
    from mcp_servers.business.app import mcp as business
    from mcp_servers.rag import tools as _rt  # noqa: F401
    from mcp_servers.rag.app import mcp as rag

    return Gateway(InProcessTransport({"business": business, "rag": rag}))
