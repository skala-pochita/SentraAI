"""rag-mcp: 보안 기준 검색, 문서 등록·수정, 오탐 피드백 관리 (명세 9.4)."""

from __future__ import annotations

from mcp_servers.common import create_server

mcp = create_server(
    "rag-mcp",
    8102,
    "보안 지식 MCP 서버. OWASP/CWE/Spring Security/사내 정책 검색, 지식 문서 관리, 오탐 규칙 관리.",
)


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
