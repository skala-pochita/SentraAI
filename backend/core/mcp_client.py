"""워커 → MCP 서버 호출 래퍼 (명세 8.3 오류 표 아래 설명, 9.1).

- 서비스 토큰 + 맥락 헤더(X-Actor-Id/X-Actor-Role/X-Audit-Id)
- H3 도구 게이트(노드별 allowlist, 역할) → 호출 → {ok,data|error} 해석
- retryable 오류는 같은 노드 안에서 0.5s, 2s 재시도 후 RetryableNodeError (Kafka 재시도로)
- H6 tool span 기록

HttpGateway 는 streamable-http 로, InProcessGateway 는 같은 프로세스의 FastMCP 인스턴스를 직접 호출한다(테스트·평가용).
"""

from __future__ import annotations

import asyncio
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


class HttpTransport:
    def _url(self, server: str) -> str:
        s = get_settings()
        return s.business_mcp_url if server == "business" else s.rag_mcp_url

    async def raw_call(self, server: str, tool: str, args: dict[str, Any], ctx: CallCtx, timeout: float) -> str:
        headers = {"Authorization": f"Bearer {get_settings().mcp_service_token}", "X-Actor-Role": ctx.actor_role}
        if ctx.actor_id:
            headers["X-Actor-Id"] = ctx.actor_id
        if ctx.audit_id:
            headers["X-Audit-Id"] = ctx.audit_id

        async def _do() -> str:
            # 명세 9.1: Agent 는 langchain-mcp-adapters MultiServerMCPClient 로 두 MCP 서버에 연결한다.
            # 호출 맥락 헤더가 호출마다 달라 클라이언트는 호출 단위로 만든다 (stateless streamable-http).
            client = MultiServerMCPClient(
                {
                    server: {
                        "transport": "streamable_http",
                        "url": self._url(server),
                        "headers": headers,
                        "timeout": timeout,
                    }
                }
            )
            async with client.session(server) as session:
                res = await session.call_tool(tool, args)
                return _first_text(res.content)

        try:
            return await asyncio.wait_for(_do(), timeout=timeout)
        except TimeoutError as e:
            raise AppError(ErrorCode.UPSTREAM_TIMEOUT, f"MCP {server}.{tool} timed out") from e
        except AppError:
            raise
        except (OSError, ExceptionGroup) as e:  # 연결 실패 등 전송 오류
            raise AppError(ErrorCode.UPSTREAM_ERROR, f"MCP {server} unreachable: {type(e).__name__}") from e


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


def http_gateway() -> Gateway:
    return Gateway(HttpTransport())


def inprocess_gateway() -> Gateway:
    from mcp_servers.business import tools as _bt  # noqa: F401 - 도구 등록
    from mcp_servers.business.app import mcp as business
    from mcp_servers.rag import tools as _rt  # noqa: F401
    from mcp_servers.rag.app import mcp as rag

    return Gateway(InProcessTransport({"business": business, "rag": rag}))
