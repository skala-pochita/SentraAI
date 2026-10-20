from pathlib import Path

import pytest

from core.errors import AppError
from core.secret_store import SecretStore
from core.settings import get_settings


def test_vault_encryption_and_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VAULT_KEY_FILE", str(tmp_path / "keys/master.key"))
    get_settings.cache_clear()
    vault = SecretStore()
    value = "test-only-value-please-do-not-log"
    ref = vault.put("project-a:provider", value)
    assert value.encode() not in (tmp_path / "data" / ref).read_bytes()
    assert vault.get("project-a:provider", ref) == value
    with pytest.raises(AppError):
        vault.get("project-b:provider", ref)
    vault.delete(ref)
    assert not (tmp_path / "data" / ref).exists()
    get_settings.cache_clear()


async def test_proxy_budget_revoke_and_model_policy(fake_redis: object) -> None:
    from core.execution_config import ExecutionConfig
    from core.proxy_sessions import issue, record_success, reserve, revoke, usage

    config = ExecutionConfig(max_requests=2, max_total_tokens=1000)
    token = await issue("audit-a", "project-a", config, "secret-ref")
    with pytest.raises(AppError):
        await reserve(token, "other-model", 100)
    await reserve(token, config.model, 500)
    await record_success(token, 500, 200)
    await reserve(token, config.model, 100)
    with pytest.raises(AppError):
        await reserve(token, config.model, 1)
    stats = await usage("audit-a")
    assert stats["requests"] == 2 and stats["successful_requests"] == 1 and stats["charged_tokens"] == 300
    await revoke("audit-a")
    with pytest.raises(AppError):
        await reserve(token, config.model, 1)
    # Restarting a grant cannot reset audit usage limits.
    another = await issue("audit-a", "project-a", config, "secret-ref")
    with pytest.raises(AppError):
        await reserve(another, config.model, 1)


async def test_proxy_concurrent_reservations(fake_redis: object) -> None:
    import asyncio

    from core.execution_config import ExecutionConfig
    from core.proxy_sessions import issue, reserve

    config = ExecutionConfig(max_requests=3, max_total_tokens=1000)
    token = await issue("concurrent", "target", config, "ref")
    results = await asyncio.gather(*(reserve(token, config.model, 300) for _ in range(10)), return_exceptions=True)
    assert sum(isinstance(v, dict) for v in results) == 3
