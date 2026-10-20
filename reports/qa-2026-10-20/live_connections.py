"""Read-only local HTTP / MCP handshake checks. Never prints credentials or business data."""

import asyncio
import json
from pathlib import Path

import httpx
from core.mcp_client import CallCtx, HttpTransport, parse_result
from core.settings import get_settings
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main():
    results = {}
    async with httpx.AsyncClient(timeout=10) as c:
        for name, url in [
            ("api", "http://localhost:8000/api/v1/ops/health"),
            ("business", "http://localhost:8101/health"),
            ("rag", "http://localhost:8102/health"),
            ("target", "http://localhost:8201/health"),
        ]:
            try:
                r = await c.get(url)
                results[name + "_health"] = {"status_code": r.status_code}
            except Exception as e:  # noqa: BLE001 - QA records only the failure type, never credentials
                results[name + "_health"] = {"error_type": type(e).__name__}
        for name, port in [("business", 8101), ("rag", 8102)]:
            r = await c.post(f"http://localhost:{port}/mcp", json={})
            results[name + "_unauthenticated"] = {"status_code": r.status_code}
    s = get_settings()
    for name, url in [("business", s.business_mcp_url), ("rag", s.rag_mcp_url)]:
        try:
            headers = {"Authorization": f"Bearer {s.mcp_service_token}", "X-Actor-Role": "AUDITOR"}
            async with streamablehttp_client(url, headers=headers, timeout=10) as (r, w, _):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    listing = await session.list_tools()
                    results[name + "_handshake"] = {"ok": True, "tool_count": len(listing.tools)}
        except Exception as e:  # noqa: BLE001 - QA records only the failure type, never credentials
            results[name + "_handshake"] = {"ok": False, "error_type": type(e).__name__}
    for name, tool in [("business", "list_audit_targets"), ("rag", "list_documents")]:
        try:
            text = await HttpTransport().raw_call(name, tool, {}, CallCtx("qa", "AUDITOR"), 15)
            data = parse_result(text)
            results[name + "_read_tool"] = {"ok": True, "item_count": len(data)}
        except Exception as e:  # noqa: BLE001 - QA records only the failure type, never credentials
            results[name + "_read_tool"] = {"ok": False, "error_type": type(e).__name__}
    out = json.dumps(results, ensure_ascii=False, indent=2)
    Path(__file__).with_name("live-results.json").write_text(out + "\n")
    print(out)


if __name__ == "__main__":
    asyncio.run(main())
