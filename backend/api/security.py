"""비밀번호 해시·JWT (명세 15장)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import bcrypt
import jwt

from core.errors import AppError, ErrorCode
from core.settings import get_settings

ALGORITHM = "HS256"
TokenType = Literal["access", "refresh"]


def hash_password(password: str) -> str:
    rounds = get_settings().bcrypt_cost
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=rounds)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def _secret() -> str:
    secret = get_settings().jwt_secret
    if len(secret.encode()) < 32:
        raise AppError(ErrorCode.INTERNAL, "JWT_SECRET must be at least 32 bytes")
    return secret


def create_token(user_id: str, role: str, token_type: TokenType) -> tuple[str, str, datetime]:
    """(token, jti, exp) 반환."""
    s = get_settings()
    now = datetime.now(UTC)
    exp = now + (
        timedelta(minutes=s.access_token_min) if token_type == "access" else timedelta(days=s.refresh_token_days)
    )
    jti = uuid.uuid4().hex
    claims = {"sub": user_id, "role": role, "jti": jti, "exp": exp, "iat": now, "typ": token_type}
    return jwt.encode(claims, _secret(), algorithm=ALGORITHM), jti, exp


def decode_token(token: str, expected: TokenType) -> dict[str, Any]:
    try:
        claims: dict[str, Any] = jwt.decode(token, _secret(), algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as e:
        raise AppError(ErrorCode.UNAUTHORIZED, "token expired") from e
    except jwt.InvalidTokenError as e:
        raise AppError(ErrorCode.UNAUTHORIZED, "invalid token") from e
    if claims.get("typ") != expected:
        raise AppError(ErrorCode.UNAUTHORIZED, "wrong token type")
    return claims


def seconds_until(exp: int | float | datetime) -> int:
    ts = exp.timestamp() if isinstance(exp, datetime) else float(exp)
    return max(1, int(ts - datetime.now(UTC).timestamp()))
