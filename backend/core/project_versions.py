"""업로드 프로젝트 버전: 수정본 재업로드를 이전 버전과 같은 프로젝트로 묶는다."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.db.models import ExecutionSettings, Target
from core.secret_store import SecretStore


def project_id(t: Target) -> str:
    """버전을 연결하지 않은 대상(첫 업로드·내장 샘플)은 자기 ID 가 프로젝트 ID 다."""
    return str(t.config.get("project_id") or t.id)


def version(t: Target) -> int:
    return int(t.config.get("version") or 1)


def next_version_config(previous: Target) -> dict[str, Any]:
    return {
        "project_id": project_id(previous),
        "version": version(previous) + 1,
        "previous_target_id": str(previous.id),
    }


async def project_ids(db: AsyncSession, target_ids: set[uuid.UUID]) -> set[str]:
    """에이전트 대상은 연결된 업로드 소스의 프로젝트로 본다 (소스·에이전트가 한 프로젝트)."""
    out = set()
    for tid in target_ids:
        t = await db.get(Target, tid)
        if t is None:
            continue
        if t.type == "agent" and t.linked_target_id and (repo := await db.get(Target, t.linked_target_id)) is not None:
            t = repo
        out.add(project_id(t))
    return out


async def copy_execution_settings(db: AsyncSession, source: uuid.UUID, dest: uuid.UUID) -> list[str]:
    """실행 설정을 새 버전으로 복사한다. 비밀값은 참조를 공유하지 않고 새 대상 범위로 다시 암호화한다.

    반환: 새로 만든 비밀 참조 (호출자가 실패 시 정리).
    """
    row = await db.get(ExecutionSettings, source)
    if row is None:
        return []
    vault = SecretStore()
    created: list[str] = []
    refs: dict[str, Any] = {}
    try:
        for name, ref in row.secret_refs.items():
            value = await asyncio.to_thread(vault.get, f"{source}:{name}", ref)
            refs[name] = await asyncio.to_thread(vault.put, f"{dest}:{name}", value)
            created.append(refs[name])
        db.add(ExecutionSettings(target_id=dest, config=dict(row.config), secret_refs=refs))
        await db.flush()
    except BaseException:
        for ref in created:
            await asyncio.to_thread(vault.delete, ref)
        raise
    return created
