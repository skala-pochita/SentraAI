"""CLI 로컬 모드 (AUTH_MODE=local): 로그인 없이 고정 사용자 'User' 로 동작한다.

인증이 없으므로 이 컴퓨터의 브라우저와 CLI 요청만 받는다.
- Host 검사: DNS 리바인딩(악성 도메인을 127.0.0.1 로 돌리는 공격)을 막는다. nginx 를 거친 요청은 X-Forwarded-Host 로 원래 Host 를 본다.
- Origin·Sec-Fetch-Site 검사: 사용자가 연 다른 웹페이지가 localhost 로 요청을 보내는 것을 막는다.
- 상태를 바꾸는 요청은 X-Sentra-Local 헤더를 요구한다. 다른 출처에서 이 헤더를 붙이면 CORS preflight 가 생기고, API 는 CORS 를 허용하지 않으므로 브라우저가 막는다.
로컬 모드 compose 는 nginx 포트 하나만 127.0.0.1 에 열고 API 포트는 열지 않는다.
"""

from __future__ import annotations

import secrets
from collections.abc import Mapping
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.models import User
from core.settings import get_settings

LOCAL_EMAIL = "user@sentraai.local"
LOCAL_NAME = "User"
LOCAL_ROLE = "ADMIN"
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
LOCAL_HEADER = "x-sentra-local"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def is_local() -> bool:
    return get_settings().auth_mode == "local"


def _hostname(host: str) -> str:
    return (urlsplit(f"//{host}").hostname or "").lower()


def reject_reason(method: str, headers: Mapping[str, str]) -> str | None:
    """로컬 모드에서 받으면 안 되는 요청이면 사유, 받아도 되면 None."""
    host = headers.get("x-forwarded-host") or headers.get("host", "")
    if _hostname(host) not in LOCAL_HOSTS:
        return "host not allowed"
    origin = headers.get("origin")
    if origin is not None and (urlsplit(origin).hostname or "").lower() not in LOCAL_HOSTS:
        return "origin not allowed"
    if headers.get("sec-fetch-site") == "cross-site":
        return "cross-site request"
    if method.upper() not in SAFE_METHODS and headers.get(LOCAL_HEADER) != "1":
        return f"missing {LOCAL_HEADER} header"
    return None


async def local_user(db: AsyncSession) -> User:
    """고정 로컬 사용자. 없으면 만든다. 비밀번호는 알 수 없는 임의 값이라 로그인에 쓸 수 없다."""
    user = (await db.execute(select(User).where(User.email == LOCAL_EMAIL))).scalar_one_or_none()
    if user is None:
        from api.security import hash_password

        user = User(
            email=LOCAL_EMAIL, name=LOCAL_NAME, role=LOCAL_ROLE, password_hash=hash_password(secrets.token_hex(32))
        )
        db.add(user)
        await db.commit()
    return user
