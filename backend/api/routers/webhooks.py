"""/webhooks/github: X-Hub-Signature-256 HMAC 검증 → Kafka github.webhook (명세 14장, GIT_MODE=github)."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from fastapi import APIRouter, Header, Request
from sqlalchemy import select

from core.db.models import FixProposalRow
from core.db.session import session_scope
from core.errors import AppError, ErrorCode
from core.kafka.envelope import Envelope
from core.kafka.producer import publish
from core.settings import get_settings

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def verify_signature(secret: str, body: bytes, signature: str | None) -> bool:
    if not secret or not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/github", status_code=202)
async def github(request: Request, x_hub_signature_256: str | None = Header(default=None)) -> dict[str, Any]:
    body = await request.body()
    if not verify_signature(get_settings().github_webhook_secret, body, x_hub_signature_256):
        raise AppError(ErrorCode.UNAUTHORIZED, "invalid webhook signature")
    payload = json.loads(body or b"{}")
    branch = payload.get("pull_request", {}).get("head", {}).get("ref", "")
    audit_id = None
    async with session_scope() as db:
        p = (await db.execute(select(FixProposalRow).where(FixProposalRow.branch == branch))).scalars().first()
        if p is not None:
            audit_id = str(p.audit_id)
            if payload.get("action") == "closed" and payload.get("pull_request", {}).get("merged"):
                p.merge_status = "MERGED"
    repo = payload.get("repository", {}).get("full_name", "unknown")
    await publish(
        "github.webhook",
        repo,
        Envelope(
            type="github.webhook",
            payload={
                "action": payload.get("action"),
                "pull_request": {"merged": payload.get("pull_request", {}).get("merged"), "head": {"ref": branch}},
                "audit_id": audit_id,
            },
        ),
    )
    return {"accepted": True, "audit_id": audit_id}
