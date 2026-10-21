"""Remove declared secrets and active temporary model tokens from target output."""

import asyncio
import hashlib
import re
from typing import Any

from core.db.session import session_scope
from core.execution_config import settings_for_agent
from core.redis.client import get_redis
from core.secret_store import SecretStore


async def redact_runtime(target: Any, audit_id: str, value: Any) -> Any:
    private: list[str] = []
    async with session_scope() as db:
        row = await settings_for_agent(db, target)
        if row is None:
            refs = {}
        else:
            refs = row.secret_refs
        for name, ref in refs.items():
            if name != "provider" and row is not None:
                private.append(await asyncio.to_thread(SecretStore().get, str(row.target_id) + ":" + name, ref))
    index = await get_redis().get("llm:audit:" + audit_id)
    if isinstance(index, bytes):
        index = index.decode()

    def clean(item: Any) -> Any:
        if isinstance(item, str):
            for secret in private:
                item = item.replace(secret, "[REDACTED]")
            if index:
                item = re.sub(
                    r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{43}(?![A-Za-z0-9_-])",
                    lambda m: (
                        "[REDACTED]" if "llm:grant:" + hashlib.sha256(m[0].encode()).hexdigest() == index else m[0]
                    ),
                    item,
                )
            return item
        if isinstance(item, list):
            return [clean(v) for v in item]
        if isinstance(item, dict):
            return {k: clean(v) for k, v in item.items()}
        return item

    return clean(value)
