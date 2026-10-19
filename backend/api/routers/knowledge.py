"""/knowledge/documents (명세 14장) — rag-mcp 도구 호출."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from api.deps import Admin, CurrentUser, LoggedIn
from core.mcp_client import CallCtx, http_gateway

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


def _ctx(user: CurrentUser) -> CallCtx:
    return CallCtx("api", user.role, str(user.id))


class DocIn(BaseModel):
    title: str
    source_type: str
    content_md: str
    ref_code: str | None = None


class DocUpdate(BaseModel):
    content_md: str


@router.get("/documents")
async def list_docs(source_type: str | None = None, user: CurrentUser = LoggedIn) -> Any:
    return await http_gateway().call("list_documents", {"source_type": source_type}, _ctx(user))


@router.get("/documents/{doc_id}")
async def get_doc(doc_id: str, user: CurrentUser = LoggedIn) -> Any:
    return await http_gateway().call("get_document", {"doc_id": doc_id}, _ctx(user))


@router.post("/documents", status_code=201)
async def create_doc(body: DocIn, user: CurrentUser = Admin) -> Any:
    return await http_gateway().call("ingest_document", body.model_dump(), _ctx(user))


@router.put("/documents/{doc_id}")
async def update_doc(doc_id: str, body: DocUpdate, user: CurrentUser = Admin) -> Any:
    return await http_gateway().call("update_document", {"doc_id": doc_id, "content_md": body.content_md}, _ctx(user))


@router.delete("/documents/{doc_id}")
async def delete_doc(doc_id: str, user: CurrentUser = Admin) -> Any:
    return await http_gateway().call("delete_document", {"doc_id": doc_id}, _ctx(user))


@router.get("/fp-rules")
async def fp_rules(target_id: str | None = None, user: CurrentUser = LoggedIn) -> Any:
    return await http_gateway().call("list_false_positive_rules", {"target_id": target_id}, _ctx(user))
