"""시드: 사용자 3명, 대상 2개(+연결), 지식 문서 인덱싱. python -m api.seed"""

from __future__ import annotations

import asyncio
import logging
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.local_mode import local_user
from api.security import hash_password
from core.db.models import Target, User
from core.db.session import session_scope
from core.settings import get_settings
from mcp_servers.rag.ingest import ingest_dir

log = logging.getLogger("seed")

REPO_TARGET = "vuln-spring-app"
AGENT_TARGET = "cs-helper"


async def seed_users(db: AsyncSession) -> dict[str, User]:
    s = get_settings()
    specs = [
        ("admin@demo.io", "김보안", "ADMIN", s.seed_admin_password),
        ("dev@demo.io", "이개발", "DEVELOPER", s.seed_dev_password),
        ("auditor@demo.io", "박감사", "AUDITOR", s.seed_auditor_password),
    ]
    out: dict[str, User] = {}
    for email, name, role, pw in specs:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is None:
            user = User(email=email, name=name, role=role, password_hash=hash_password(pw))
            db.add(user)
            await db.flush()
        out[role] = user
    return out


async def seed_targets(db: AsyncSession, owner: User) -> tuple[Target, Target]:
    s = get_settings()
    repo = (await db.execute(select(Target).where(Target.name == REPO_TARGET))).scalar_one_or_none()
    if repo is None:
        repo = Target(
            type="repo",
            name=REPO_TARGET,
            ref=str(s.workspace_dir / REPO_TARGET),
            owner_user_id=owner.id,
            config={"default_branch": "main"},
        )
        db.add(repo)
        await db.flush()
    agent = (await db.execute(select(Target).where(Target.name == AGENT_TARGET))).scalar_one_or_none()
    if agent is None:
        agent = Target(
            type="agent",
            name=AGENT_TARGET,
            ref=s.target_agent_url,
            owner_user_id=owner.id,
            linked_target_id=repo.id,
            config={"config_dir": str(s.workspace_dir / "agent-config"), "backend_api_of": str(repo.id)},
        )
        db.add(agent)
        await db.flush()
    repo.linked_target_id = agent.id
    return repo, agent


async def main(local: bool = False) -> None:
    logging.basicConfig(level=logging.INFO)
    if local:
        # CLI 로컬 모드: 데모 계정·대상 없이 로컬 사용자와 보안 기준 문서만
        async with session_scope() as db:
            await local_user(db)
            docs = await ingest_dir(db, get_settings().knowledge_dir)
            log.info("local seed: indexed knowledge documents: %s", docs)
        return
    async with session_scope() as db:
        users = await seed_users(db)
        repo, agent = await seed_targets(db, users["DEVELOPER"])
        log.info("seeded users=%s targets=%s,%s", list(users), repo.id, agent.id)
    async with session_scope() as db:
        docs = await ingest_dir(db, get_settings().knowledge_dir)
        log.info("indexed knowledge documents: %s", docs)


if __name__ == "__main__":
    asyncio.run(main(local="--local" in sys.argv[1:]))
