"""Execution configuration: public values and write-only secret input."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import CurrentUser, LoggedIn, ensure_target_access, require_roles
from core.db.models import Audit, AuditTarget, ExecutionSettings, Target
from core.db.session import get_db
from core.errors import AppError, ErrorCode
from core.execution_config import ExecutionConfig, ExecutionUpdate, public_settings, readiness, validate_config
from core.secret_store import SecretStore

router = APIRouter(tags=["execution"])


async def uploaded_repo(db: AsyncSession, user: CurrentUser, tid: UUID) -> Target:
    await ensure_target_access(user, db, {tid})
    target = await db.get(Target, tid)
    if target is None or target.type != "repo" or target.config.get("source") != "upload":
        raise AppError(ErrorCode.NOT_FOUND, "업로드 프로젝트를 찾을 수 없습니다.")
    return target


@router.get("/targets/{tid}/execution")
async def get_execution(tid: UUID, user: CurrentUser = LoggedIn, db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    target = await uploaded_repo(db, user, tid)
    row = await db.get(ExecutionSettings, tid)
    return public_settings(row) | {"readiness": await asyncio.to_thread(readiness, Path(target.ref), row)}


@router.put("/targets/{tid}/execution")
async def save_execution(
    tid: UUID,
    body: ExecutionUpdate,
    user: CurrentUser = Depends(require_roles("ADMIN", "DEVELOPER")),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    target = await uploaded_repo(db, user, tid)
    validate_config(body)
    await db.get(Target, tid, with_for_update=True)
    active = (
        await db.execute(
            select(Audit.id)
            .join(AuditTarget, AuditTarget.audit_id == Audit.id)
            .where(
                AuditTarget.target_id.in_([tid] + ([target.linked_target_id] if target.linked_target_id else [])),
                Audit.status.in_(["RUNNING", "QUEUED"]),
            )
        )
    ).first()
    if active:
        raise AppError(ErrorCode.CONFLICT, "진행 중인 감사가 있어 실행 설정을 변경할 수 없습니다.")
    row = await db.get(ExecutionSettings, tid, with_for_update=True)
    refs = dict(row.secret_refs if row else {})
    old: list[str] = []
    created: list[str] = []
    vault = SecretStore()
    try:
        values = {f"{s}:{k}": v.get_secret_value() for s, group in body.secrets.items() for k, v in group.items()}
        if body.provider_api_key is not None:
            values["provider"] = body.provider_api_key.get_secret_value()
        for name in body.remove_secrets + (["provider"] if body.remove_provider_key else []):
            if name in refs:
                old.append(refs.pop(name))
        for name, value in values.items():
            newref = await asyncio.to_thread(vault.put, str(tid) + ":" + name, value)
            created.append(newref)
            if name in refs:
                old.append(refs[name])
            refs[name] = newref
        config = ExecutionConfig.model_validate(body.model_dump()).model_dump()
        if row is None:
            row = ExecutionSettings(target_id=tid, config=config, secret_refs=refs)
            db.add(row)
        else:
            row.config, row.secret_refs = config, refs
        await db.commit()
    except Exception:
        for ref in created:
            await asyncio.to_thread(vault.delete, ref)
        raise
    for ref in old:
        await asyncio.to_thread(vault.delete, ref)
    return public_settings(row) | {"readiness": await asyncio.to_thread(readiness, Path(target.ref), row)}
