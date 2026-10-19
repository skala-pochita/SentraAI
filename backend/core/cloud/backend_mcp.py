"""제품 경로 CloudBackend: 워커 → (H3 게이트·서비스 토큰) → business-mcp / rag-mcp. 워커는 업무 DB 를 직접 읽지 않는다."""

from __future__ import annotations

from typing import Any

from core.mcp_client import CallCtx, Gateway


class McpBackend:
    def __init__(self, gateway: Gateway, task_id: str, actor_id: str | None, actor_role: str = "ADMIN") -> None:
        self.gw, self.task_id = gateway, task_id
        self.ctx = CallCtx(node="cloud", actor_role=actor_role, actor_id=actor_id, audit_id=None)

    async def get_audit_evidence(self, evidence_ids: list[str] | None, kinds: list[str] | None) -> dict[str, Any]:
        return await self.gw.call(  # type: ignore[no-any-return]
            "get_audit_evidence", {"task_id": self.task_id, "evidence_ids": evidence_ids, "kinds": kinds}, self.ctx
        )

    async def get_finding_status(self, finding_ids: list[str] | None) -> dict[str, Any]:
        return await self.gw.call("get_remediation_status", {"task_id": self.task_id}, self.ctx)  # type: ignore[no-any-return]

    async def create_remediation_ticket(
        self, task_id: str, action_id: str, args: dict[str, Any], approval_ref: str | None, args_hash: str | None
    ) -> dict[str, Any]:
        return await self.gw.call(  # type: ignore[no-any-return]
            "create_remediation_ticket",
            {
                "task_id": task_id,
                "action_id": action_id,
                "args": args,
                "approval_ref": approval_ref,
                "args_hash_claimed": args_hash,
            },
            self.ctx,
        )

    async def search_guidance(self, query: str, top_k: int) -> list[dict[str, Any]]:
        return await self.gw.call("search_security_guidance", {"query": query, "k": top_k}, self.ctx)  # type: ignore[no-any-return]

    async def read_resource(self, uri: str) -> dict[str, Any]:
        text = await self.gw.read_resource("rag", uri, self.ctx)
        return {"uri": uri, "version": "v1", "text": text}
