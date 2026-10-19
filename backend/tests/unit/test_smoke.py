from fastapi.testclient import TestClient

from api.main import app
from core.settings import load_config


def test_health() -> None:
    res = TestClient(app).get("/api/v1/ops/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_config_env_substitution(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    load_config.cache_clear()
    monkeypatch.setenv("LLM_SMALL_MODEL", "nano-x")
    cfg = load_config("models")
    assert cfg["defaults"]["model"] == "nano-x"
    load_config.cache_clear()


def test_mcp_servers_importable() -> None:
    from mcp_servers.business.server import mcp as biz
    from mcp_servers.rag.server import mcp as rag

    assert biz.name == "business-mcp"
    assert rag.name == "rag-mcp"
