"""rag-mcp: 보안 기준 검색, 문서 등록·수정, 오탐 피드백 관리 (명세 9.4)."""

from __future__ import annotations

from mcp_servers.common import run_server
from mcp_servers.rag import tools  # noqa: F401 - 도구 등록
from mcp_servers.rag.app import PORT, mcp


def main() -> None:
    run_server(mcp, PORT)


if __name__ == "__main__":
    main()
