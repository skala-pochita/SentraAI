"""감사 대상 해석과 작업공간 접근 (경로 탈출 방지, git)."""

from __future__ import annotations

import asyncio
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import select

from core.db.models import Target
from core.db.session import session_scope
from core.errors import AppError, ErrorCode


@dataclass(frozen=True)
class TargetInfo:
    id: str
    type: str
    name: str
    ref: str
    config: dict[str, Any] = field(default_factory=dict)
    linked_target_id: str | None = None
    owner_user_id: str | None = None

    @property
    def root(self) -> Path:
        if self.type == "repo":
            return Path(self.ref)
        return Path(self.config.get("config_dir", "/workspace/agent-config"))


# 테스트용 대상 주입 (DB 없이)
_overrides: dict[str, TargetInfo] = {}


def register_override(t: TargetInfo) -> None:
    _overrides[t.id] = t


def clear_overrides() -> None:
    _overrides.clear()


def _info(t: Target) -> TargetInfo:
    return TargetInfo(
        str(t.id),
        t.type,
        t.name,
        t.ref,
        dict(t.config or {}),
        str(t.linked_target_id) if t.linked_target_id else None,
        str(t.owner_user_id) if t.owner_user_id else None,
    )


async def get_target(target_id: str, expected_type: str | None = None) -> TargetInfo:
    t = _overrides.get(target_id)
    if t is None:
        try:
            tid = uuid.UUID(target_id)
        except ValueError as e:
            raise AppError(ErrorCode.VALIDATION, "target_id must be a UUID") from e
        async with session_scope() as db:
            row = await db.get(Target, tid)
            if row is None:
                raise AppError(ErrorCode.NOT_FOUND, f"target {target_id} not found")
            t = _info(row)
    if expected_type and t.type != expected_type:
        raise AppError(ErrorCode.VALIDATION, f"target {target_id} is {t.type}, expected {expected_type}")
    return t


async def list_targets(type_: str | None = None) -> list[TargetInfo]:
    if _overrides:
        return [t for t in _overrides.values() if not type_ or t.type == type_]
    async with session_scope() as db:
        q = select(Target).order_by(Target.created_at)
        if type_:
            q = q.where(Target.type == type_)
        return [_info(t) for t in (await db.execute(q)).scalars()]


def safe_path(root: Path, rel: str) -> Path:
    """상대 경로만 허용. 절대경로·'..'·.git·루트 밖 심볼릭 링크 거부 (VALIDATION)."""
    if not rel or "\x00" in rel or "\\" in rel:
        raise AppError(ErrorCode.VALIDATION, "invalid path")
    pp = PurePosixPath(rel)
    if pp.is_absolute() or rel.startswith("~") or ".." in pp.parts:
        raise AppError(ErrorCode.VALIDATION, "path traversal rejected", {"path": rel})
    if ".git" in pp.parts:
        raise AppError(ErrorCode.VALIDATION, ".git access rejected")
    root_r = root.resolve()
    full = (root_r / pp).resolve()
    if not full.is_relative_to(root_r):
        raise AppError(ErrorCode.VALIDATION, "path escapes target root", {"path": rel})
    return full


def run_git(
    root: Path, *args: str, timeout: float = 15, check: bool = True, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=check,  # noqa: S607
            input=input_text,
        )
    except subprocess.TimeoutExpired as e:
        raise AppError(ErrorCode.UPSTREAM_TIMEOUT, f"git {args[0]} timed out") from e
    except subprocess.CalledProcessError as e:
        raise AppError(ErrorCode.CONFLICT, f"git {args[0]} failed", {"stderr": (e.stderr or "")[-500:]}) from e


async def git(
    root: Path, *args: str, timeout: float = 15, check: bool = True, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    return await asyncio.to_thread(run_git, root, *args, timeout=timeout, check=check, input_text=input_text)


async def commit_sha(root: Path) -> str:
    if not (root / ".git").exists():
        return "nogit"
    res = await git(root, "rev-parse", "HEAD", check=False)
    return res.stdout.strip() or "nogit"
