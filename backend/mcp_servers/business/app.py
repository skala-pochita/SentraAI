"""business-mcp FastMCP 인스턴스 (도구 모듈들이 여기에 등록된다)."""

from __future__ import annotations

from mcp_servers.common import create_server

PORT = 8101

mcp = create_server(
    "business-mcp",
    PORT,
    "감사 업무 MCP 서버. 감사 대상(레포·에이전트) 조회, 정적 스캔·Agent 규칙·인젝션 probe 실행, "
    "Finding·승인·수정안·위험 수용·보고서 관리. 모든 응답은 {ok, data|error, meta} 형식.",
)
