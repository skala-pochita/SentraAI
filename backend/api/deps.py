"""FastAPI 의존성: 인증 사용자, 역할 검사, 리소스 범위 (명세 15장)."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.security import decode_token
from core.db.models import Target, User
from core.db.session import get_db
from core.errors import AppError, ErrorCode
from core.redis.client import get_redis


@dataclass(frozen=True)
class CurrentUser:
    id: uuid.UUID
    email: str
    name: str
    role: str
    jti: str
    exp: int

    @property
    def is_admin(self) -> bool:
        return self.role == "ADMIN"


def _bearer(request: Request, token_q: str | None) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    if token_q:  # EventSource 는 헤더를 못 붙이므로 ?token= 허용
        return token_q
    raise AppError(ErrorCode.UNAUTHORIZED, "missing bearer token")


async def get_current_user(
    request: Request,
    token: str | None = Query(default=None, include_in_schema=False),
    db: AsyncSession = Depends(get_db),
) -> CurrentUser:
    claims = decode_token(_bearer(request, token), "access")
    if await get_redis().exists(f"auth:blacklist:{claims['jti']}"):
        raise AppError(ErrorCode.UNAUTHORIZED, "token revoked")
    user = await db.get(User, uuid.UUID(claims["sub"]))
    if user is None or not user.is_active:
        raise AppError(ErrorCode.UNAUTHORIZED, "user inactive")
    return CurrentUser(user.id, user.email, user.name, user.role, claims["jti"], int(claims["exp"]))


def require_roles(*roles: str) -> Callable[..., Awaitable[CurrentUser]]:
    async def dep(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in roles:
            raise AppError(ErrorCode.FORBIDDEN, f"requires role {'/'.join(roles)}")
        return user

    return dep


async def accessible_target_ids(user: CurrentUser, db: AsyncSession) -> set[uuid.UUID] | None:
    """DEVELOPER 는 자기 소유 대상(+연결된 대상)만. None = 전체 접근."""
    if user.role != "DEVELOPER":
        return None
    rows = (await db.execute(select(Target.id, Target.linked_target_id).where(Target.owner_user_id == user.id))).all()
    ids = {r.id for r in rows} | {r.linked_target_id for r in rows if r.linked_target_id}
    return ids


async def ensure_target_access(user: CurrentUser, db: AsyncSession, target_ids: set[uuid.UUID]) -> None:
    allowed = await accessible_target_ids(user, db)
    if allowed is not None and not target_ids <= allowed:
        raise AppError(ErrorCode.FORBIDDEN, "target not accessible")


Admin = Depends(require_roles("ADMIN"))
LoggedIn = Depends(get_current_user)
