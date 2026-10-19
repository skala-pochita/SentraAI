"""MCP 서버 공통: FastMCP 생성, /health, 공통 응답·오류 스키마 (명세 9.2)."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse


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
