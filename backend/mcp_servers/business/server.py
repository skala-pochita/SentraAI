"""business-mcp: 감사 대상 조회, 스캔·probe 실행, Finding·승인·수정·보고서 변경 (명세 9.3)."""

from __future__ import annotations

from mcp_servers.business import tools  # noqa: F401 - 도구 등록
from mcp_servers.business.app import PORT, mcp
from mcp_servers.common import run_server


def main() -> None:
    run_server(mcp, PORT)


if __name__ == "__main__":
    main()
