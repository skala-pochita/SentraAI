"""Original-backed regulation discovery and exact retrieval."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context

from mcp_servers.common import mcp_tool
from mcp_servers.rag.app import mcp
from mcp_servers.rag.regulations import exact_clause, search_clauses


@mcp_tool(mcp)
async def search_regulation(query: str, source: str | None = None, k: int = 5, ctx: Context | None = None) -> Any:
    """규제 후보 조항 검색. 인용 전 get_clause로 재조회한다."""
    return await search_clauses(query, source, k)


@mcp_tool(mcp)
async def get_clause(clause_id: str, ctx: Context | None = None) -> Any:
    """ID로 원문과 해시를 정확 조회. 원문/인덱스 불일치는 VALIDATION으로 차단."""
    return await exact_clause(clause_id)
