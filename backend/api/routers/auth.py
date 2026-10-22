"""/auth/*, /me, /users (명세 14·15장)."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import Admin, CurrentUser, LoggedIn
from api.errors import Page, page_params, paged
from api.security import create_token, decode_token, hash_password, seconds_until, verify_password
from core.db.models import User
from core.db.session import get_db
from core.errors import AppError, ErrorCode
from core.redis.client import get_redis
from core.settings import get_settings

router = APIRouter(tags=["auth"])

MAX_FAILS = 5
FAIL_WINDOW_S = 15 * 60


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    role: str
    is_active: bool = True

    @classmethod
    def of(cls, u: User) -> UserOut:
        return cls(id=u.id, email=u.email, name=u.name, role=u.role, is_active=u.is_active)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class LogoutIn(BaseModel):
    refresh_token: str | None = None


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserOut


class UserCreate(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1)
    password: str = Field(min_length=8)
    role: Literal["ADMIN", "DEVELOPER", "AUDITOR"]


async def _issue(user: User) -> TokenOut:
    access, _, _ = create_token(str(user.id), user.role, "access")
    refresh, jti, _ = create_token(str(user.id), user.role, "refresh")
    ttl = get_settings().refresh_token_days * 86400
    await get_redis().set(f"auth:refresh:{jti}", str(user.id), ex=ttl)
    return TokenOut(access_token=access, refresh_token=refresh, user=UserOut.of(user))


@router.post("/auth/login", response_model=TokenOut)
async def login(body: LoginIn, db: AsyncSession = Depends(get_db)) -> TokenOut:
    r = get_redis()
    email = body.email.lower()
    fail_key = f"auth:fail:{email}"
    fails = int(await r.get(fail_key) or 0)
    if fails >= MAX_FAILS:
        raise AppError(
            ErrorCode.RATE_LIMITED, "too many failed logins; locked for 15 minutes", retry_after_s=await r.ttl(fail_key)
        )
    user = (await db.execute(select(User).where(func.lower(User.email) == email))).scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        n = await r.incr(fail_key)
        if n == 1:
            await r.expire(fail_key, FAIL_WINDOW_S)
        raise AppError(ErrorCode.UNAUTHORIZED, "invalid email or password")
    await r.delete(fail_key)
    return await _issue(user)


@router.post("/auth/refresh", response_model=TokenOut)
async def refresh(body: RefreshIn, db: AsyncSession = Depends(get_db)) -> TokenOut:
    claims = decode_token(body.refresh_token, "refresh")
    r = get_redis()
    # 회전: 기존 jti 를 원자적으로 폐기. 이미 없으면 재사용 시도로 간주.
    owner = await r.getdel(f"auth:refresh:{claims['jti']}")
    if owner is None or owner != claims["sub"]:
        raise AppError(ErrorCode.UNAUTHORIZED, "refresh token revoked")
    user = await db.get(User, uuid.UUID(claims["sub"]))
    if user is None or not user.is_active:
        raise AppError(ErrorCode.UNAUTHORIZED, "user inactive")
    return await _issue(user)


@router.post("/auth/logout", status_code=204)
async def logout(body: LogoutIn | None = None, user: CurrentUser = LoggedIn) -> None:
    r = get_redis()
    await r.set(f"auth:blacklist:{user.jti}", "1", ex=seconds_until(user.exp))
    if body and body.refresh_token:
        try:
            claims = decode_token(body.refresh_token, "refresh")
        except AppError:
            return
        if claims["sub"] == str(user.id):
            await r.delete(f"auth:refresh:{claims['jti']}")


@router.get("/auth/mode")
async def auth_mode() -> dict[str, str]:
    """프론트가 로그인 화면을 보일지 정한다. local = CLI 로컬 모드 (로그인 없음)."""
    return {"mode": get_settings().auth_mode}


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser = LoggedIn, db: AsyncSession = Depends(get_db)) -> UserOut:
    row = await db.get(User, user.id)
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "user not found")
    return UserOut.of(row)


@router.get("/users")
async def list_users(
    _: CurrentUser = Admin, p: Page = Depends(page_params), db: AsyncSession = Depends(get_db)
) -> dict[str, object]:
    total = (await db.execute(select(func.count()).select_from(User))).scalar_one()
    rows = (await db.execute(select(User).order_by(User.created_at).offset(p.offset).limit(p.size))).scalars()
    return paged([UserOut.of(u) for u in rows], total, p)


@router.post("/users", response_model=UserOut, status_code=201)
async def create_user(body: UserCreate, _: CurrentUser = Admin, db: AsyncSession = Depends(get_db)) -> UserOut:
    user = User(email=body.email.lower(), name=body.name, role=body.role, password_hash=hash_password(body.password))
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as e:
        raise AppError(ErrorCode.CONFLICT, "email already exists") from e
    await db.refresh(user)
    return UserOut.of(user)
