"""공용 픽스처. 통합 테스트는 로컬 Postgres(docker compose)의 auditor_test DB 를 사용하고,
연결할 수 없으면 skip 한다. Redis 는 fakeredis."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator

import pytest

from tests.compliance_fixtures import originals  # noqa: F401 - shared fixture

os.environ.setdefault("BCRYPT_COST", "4")
os.environ.setdefault("JWT_SECRET", "test-secret-test-secret-test-secret-0123")

TEST_DB = os.environ.get("TEST_DATABASE_URL", "postgresql+asyncpg://auditor:auditor@localhost:5432/auditor_test")
ADMIN_DB = TEST_DB.rsplit("/", 1)[0] + "/auditor"


def _clear_caches() -> None:
    from core.db.session import get_engine, get_sessionmaker
    from core.settings import get_settings, load_config

    for f in (get_settings, load_config, get_engine, get_sessionmaker):
        f.cache_clear()


async def _prepare_db() -> bool:
    import asyncpg

    try:
        conn = await asyncpg.connect(ADMIN_DB.replace("+asyncpg", ""), timeout=2)
    except (OSError, asyncpg.PostgresError, TimeoutError):
        return False
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname='auditor_test'")
        if not exists:
            await conn.execute("CREATE DATABASE auditor_test")
    finally:
        await conn.close()

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from core.db.models import Base

    engine = create_async_engine(TEST_DB)
    async with engine.begin() as c:
        await c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await c.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        # 테스트 전용 DB: 이전 스키마(예: 삭제된 Cloud 테이블)가 남아도 깨끗이 다시 만든다
        await c.execute(text("DROP SCHEMA public CASCADE"))
        await c.execute(text("CREATE SCHEMA public"))
        await c.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await c.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        await c.run_sync(Base.metadata.create_all)
    await engine.dispose()
    return True


@pytest.fixture(scope="session")
def pg_available() -> bool:
    return asyncio.run(_prepare_db())


@pytest.fixture
def fake_redis() -> Iterator[object]:
    import fakeredis

    from core.redis.client import set_redis

    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    set_redis(client)
    yield client
    set_redis(None)


@pytest.fixture
async def db_ready(pg_available: bool, monkeypatch: pytest.MonkeyPatch, fake_redis: object) -> AsyncIterator[None]:
    if not pg_available:
        pytest.skip("postgres not reachable (run `docker compose up -d postgres`)")
    monkeypatch.setenv("DATABASE_URL", TEST_DB)
    _clear_caches()
    from sqlalchemy import text

    from core.db.models import Base
    from core.db.session import get_engine

    async with get_engine().begin() as c:
        names = ", ".join(t.name for t in Base.metadata.sorted_tables)
        await c.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))
    yield
    await get_engine().dispose()
    _clear_caches()


@pytest.fixture
async def client(db_ready: None) -> AsyncIterator[object]:
    import httpx

    from api.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c
