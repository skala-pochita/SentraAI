"""rag-mcp FastMCP 인스턴스."""

from __future__ import annotations

from mcp_servers.common import create_server

PORT = 8102

mcp = create_server(
    "rag-mcp",
    PORT,
    "보안 지식 MCP 서버. OWASP Top10/LLM/Agentic·CWE·Spring Security 가이드·사내 정책 하이브리드 검색, "
    "지식 문서 등록·수정·삭제, 오탐 억제 규칙 관리. 모든 응답은 {ok, data|error, meta} 형식.",
)
