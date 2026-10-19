"""/targets (명세 14장)."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import Admin, CurrentUser, LoggedIn, accessible_target_ids
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
        return cls(id=t.id, type=t.type, name=t.name, ref=t.ref, owner_user_id=t.owner_user_id,
                   linked_target_id=t.linked_target_id, config=t.config)


class TargetCreate(BaseModel):
    type: Literal["repo", "agent"]
    name: str
    ref: str
    owner_user_id: uuid.UUID | None = None
    linked_target_id: uuid.UUID | None = None
    config: dict[str, Any] = {}


@router.get("/targets")
async def list_targets(user: CurrentUser = LoggedIn, p: Page = Depends(page_params),
                       db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    q = select(Target)
    allowed = await accessible_target_ids(user, db)
    if allowed is not None:
        q = q.where(Target.id.in_(allowed or {uuid.UUID(int=0)}))
    total = (await db.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await db.execute(q.order_by(Target.created_at).offset(p.offset).limit(p.size))).scalars()
    return paged([TargetOut.of(t) for t in rows], total, p)


@router.post("/targets", response_model=TargetOut, status_code=201)
async def create_target(body: TargetCreate, _: CurrentUser = Admin,
                        db: AsyncSession = Depends(get_db)) -> TargetOut:
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
