"""business-mcp: 감사 대상 조회, 스캔·probe 실행, Finding·승인·수정·보고서 변경 (명세 9.3)."""

from __future__ import annotations

from mcp_servers.common import create_server

mcp = create_server(
    "business-mcp",
    8101,
    "감사 업무 MCP 서버. 감사 대상 조회, 정적 스캔/probe 실행, Finding·승인·수정안·보고서 관리.",
)


def main() -> None:
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
