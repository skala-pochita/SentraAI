"""/targets (명세 14장)."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Form, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import Admin, CurrentUser, LoggedIn, accessible_target_ids, ensure_target_access, require_roles
from api.errors import Page, page_params, paged
from core.db.models import Target
from core.db.session import get_db
from core.errors import AppError, ErrorCode

router = APIRouter(tags=["targets"])


class TargetOut(BaseModel):
    id: uuid.UUID
    type: str
    name: str
    ref: str
    owner_user_id: uuid.UUID | None
    linked_target_id: uuid.UUID | None
    config: dict[str, Any]

    @classmethod
    def of(cls, t: Target) -> TargetOut:
        return cls(
            id=t.id,
            type=t.type,
            name=t.name,
            ref=t.ref,
            owner_user_id=t.owner_user_id,
            linked_target_id=t.linked_target_id,
            config=t.config,
        )


class TargetCreate(BaseModel):
    type: Literal["repo", "agent"]
    name: str
    ref: str
    owner_user_id: uuid.UUID | None = None
    linked_target_id: uuid.UUID | None = None
    config: dict[str, Any] = {}


@router.get("/targets")
async def list_targets(
    user: CurrentUser = LoggedIn, p: Page = Depends(page_params), db: AsyncSession = Depends(get_db)
) -> dict[str, object]:
    q = select(Target)
    allowed = await accessible_target_ids(user, db)
    if allowed is not None:
        q = q.where(Target.id.in_(allowed or {uuid.UUID(int=0)}))
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await db.execute(q.order_by(Target.created_at).offset(p.offset).limit(p.size))).scalars()
    return paged([TargetOut.of(t) for t in rows], total, p)


@router.post("/targets", response_model=TargetOut, status_code=201)
async def create_target(body: TargetCreate, _: CurrentUser = Admin, db: AsyncSession = Depends(get_db)) -> TargetOut:
    if body.type == "repo":
        raise AppError(ErrorCode.VALIDATION, "코드 프로젝트는 ZIP 업로드로 등록하세요.")
    if body.linked_target_id and await db.get(Target, body.linked_target_id) is None:
        raise AppError(ErrorCode.VALIDATION, "linked_target_id not found")
    t = Target(**body.model_dump())
    db.add(t)
    await db.flush()
    await db.refresh(t)
    if body.linked_target_id:  # 양방향 연결
        other = await db.get(Target, body.linked_target_id)
        if other is not None and other.linked_target_id is None:
            other.linked_target_id = t.id
    return TargetOut.of(t)


@router.post("/targets/upload", response_model=TargetOut, status_code=201)
async def upload_project(
    file: UploadFile,
    previous_target_id: uuid.UUID | None = Form(None),
    user: CurrentUser = Depends(require_roles("ADMIN", "DEVELOPER")),
    db: AsyncSession = Depends(get_db),
) -> TargetOut:
    """Upload a ZIP snapshot; register a private, read-only code audit target.

    previous_target_id: 수정본이면 이전 버전의 업로드 소스 대상. 같은 프로젝트의 다음 버전으로 등록하고
    실행 설정을 복사한다 (비밀값은 새 대상 범위로 다시 암호화).
    """
    import asyncio
    import shutil

    from core.project_upload import MAX_UPLOAD_BYTES, extract_project
    from core.project_versions import copy_execution_settings, next_version_config
    from core.secret_store import SecretStore
    from core.settings import get_settings

    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise AppError(ErrorCode.VALIDATION, "프로젝트를 ZIP 파일로 업로드하세요.")
    lineage: dict[str, Any] = {}
    if previous_target_id is not None:
        await ensure_target_access(user, db, {previous_target_id})
        previous = await db.get(Target, previous_target_id)
        if previous is None or previous.type != "repo" or previous.config.get("source") != "upload":
            raise AppError(ErrorCode.VALIDATION, "이전 버전은 업로드한 소스 프로젝트여야 합니다.")
        lineage = next_version_config(previous)
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    await file.close()
    target_id = uuid.uuid4()
    destination = get_settings().workspace_dir / "uploads" / str(target_id)
    root = await asyncio.to_thread(extract_project, data, destination)
    copied: list[str] = []
    try:
        from core.agent_project import discover_agent

        profile = await asyncio.to_thread(discover_agent, root)
        agent_id = uuid.uuid4() if profile else None
        runtime = {"supported": False, "reason": "에이전트 정의 없음"}
        if profile:
            from core.runtime_spec import runtime_spec

            try:
                runtime_spec(root)
                from core.project_probes import uploaded_probe_set

                if not uploaded_probe_set(
                    "preflight", profile["manifest"], profile.get("runtime_protocol", "auditor")
                ).probes:
                    raise AppError(
                        ErrorCode.VALIDATION,
                        "이 에이전트에는 audit-runtime.yaml 및 audit-probes.yaml 공격 정의가 필요합니다.",
                    )
                runtime = {"supported": True, "reason": ""}
            except AppError as exc:
                runtime = {"supported": False, "reason": exc.message}

        t = Target(
            id=target_id,
            type="repo",
            name=file.filename.rsplit("/", 1)[-1][:-4][:200] or "project",
            ref=str(root),
            owner_user_id=user.id,
            config={
                "source": "upload",
                "read_only": True,
                "agent_detected": bool(profile),
                "runtime": runtime,
                "agent_discovery": profile["discovery"] if profile else None,
                "project_id": lineage.get("project_id", str(target_id)),
                "version": lineage.get("version", 1),
                "previous_target_id": lineage.get("previous_target_id"),
            },
        )
        db.add(t)
        await db.flush()
        if agent_id and profile:
            agent = Target(
                id=agent_id,
                type="agent",
                name=t.name + " · Agent",
                ref="",
                owner_user_id=user.id,
                linked_target_id=t.id,
                config={
                    "source": "upload",
                    "config_dir": str(root),
                    "read_only": True,
                    "discovery": profile["discovery"],
                    "runtime_status": "PENDING" if runtime["supported"] else "UNSUPPORTED",
                    "runtime_reason": runtime["reason"],
                    "runtime_protocol": profile.get("runtime_protocol", "auditor"),
                },
            )
            db.add(agent)
            await db.flush()
            t.linked_target_id = agent.id
            await db.flush()
        if previous_target_id is not None:
            copied = await copy_execution_settings(db, previous_target_id, target_id)
        await db.refresh(t)
        await db.commit()
        return TargetOut.of(t)
    except Exception:
        await asyncio.to_thread(shutil.rmtree, destination, True)
        for ref in copied:
            await asyncio.to_thread(SecretStore().delete, ref)
        raise
