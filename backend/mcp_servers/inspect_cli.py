"""간이 MCP Inspector: 도구 목록 조회·호출.

python -m mcp_servers.inspect_cli --url http://localhost:8101/mcp list
python -m mcp_servers.inspect_cli call read_repo_file '{"target_id": "...", "path": ".env"}'
"""

from __future__ import annotations

import argparse
import asyncio
import json

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from core.settings import get_settings


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default=get_settings().business_mcp_url)
    ap.add_argument("--role", default="ADMIN")
    ap.add_argument("cmd", choices=["list", "call"])
    ap.add_argument("tool", nargs="?")
    ap.add_argument("args", nargs="?", default="{}")
    a = ap.parse_args()
    headers = {
        "Authorization": f"Bearer {get_settings().mcp_service_token}",
        "X-Actor-Role": a.role,
        "X-Actor-Id": "inspect-cli",
    }
    async with streamablehttp_client(a.url, headers=headers, timeout=30) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            if a.cmd == "list":
                for t in (await session.list_tools()).tools:
                    print(f"{t.name:28} {((t.description or '').strip().splitlines() or ['(설명 없음)'])[0]}")
            else:
                res = await session.call_tool(a.tool, json.loads(a.args))
                for c in res.content:
                    print(getattr(c, "text", c))


if __name__ == "__main__":
    asyncio.run(main())
