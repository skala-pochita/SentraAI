"""Application settings (environment variables + config/*.yaml)."""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


def _default_path(env: str, rel: str) -> Path:
    return Path(os.environ.get(env, str(REPO_ROOT / rel)))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(REPO_ROOT / ".env"), extra="ignore")

    # LLM
    openai_api_key: str = ""
    llm_small_model: str = "gpt-5.4-nano"
    embedding_model: str = "text-embedding-3-small"
    langsmith_api_key: str = ""

    # Auth
    jwt_secret: str = Field(default="dev-only-jwt-secret-change-me-32bytes!!")
    access_token_min: int = 15
    refresh_token_days: int = 7
    mcp_service_token: str = "change-me"
    bcrypt_cost: int = 12

    seed_admin_password: str = "Admin!demo1234"
    seed_dev_password: str = "Dev!demo1234"
    seed_auditor_password: str = "Auditor!demo1234"

    # Infra
    database_url: str = "postgresql+asyncpg://auditor:auditor@localhost:5432/auditor"
    redis_url: str = "redis://localhost:6379/0"
    kafka_bootstrap: str = "localhost:9092"
    business_mcp_url: str = "http://localhost:8101/mcp"
    rag_mcp_url: str = "http://localhost:8102/mcp"
    target_agent_url: str = "http://localhost:8201"
    probe_allowlist: str = "http://localhost:8201"

    sandbox_url: str = "http://sandbox-runner:8300"

    sandbox_service_token: str = ""
    llm_proxy_url: str = "http://llm-proxy:8400/v1"
    llm_proxy_upstream: str = "https://api.openai.com/v1/chat/completions"
    llm_proxy_model_allowlist: str = "gpt-5.4-nano"
    vault_data_dir: Path = Field(default_factory=lambda: _default_path("VAULT_DATA_DIR", "private/vault/data"))
    vault_key_file: Path = Field(default_factory=lambda: _default_path("VAULT_KEY_FILE", "private/vault/keys/master.key"))

    # Demo
    agent_variant: Literal["vuln", "hardened"] = "vuln"
    fault_injection: str = ""
    probe_attempts: int = 3
    workspace_dir: Path = Field(default_factory=lambda: _default_path("WORKSPACE_DIR", "workspace"))

    # Paths
    config_dir: Path = Field(default_factory=lambda: _default_path("CONFIG_DIR", "config"))
    rules_dir: Path = Field(default_factory=lambda: _default_path("RULES_DIR", "rules"))
    datasets_dir: Path = Field(default_factory=lambda: _default_path("DATASETS_DIR", "datasets"))
    knowledge_dir: Path = Field(default_factory=lambda: _default_path("KNOWLEDGE_DIR", "knowledge"))
    reports_dir: Path = Field(default_factory=lambda: _default_path("REPORTS_DIR", "reports"))
    repo_targets_dir: Path = Field(default_factory=lambda: _default_path("TARGETS_DIR", "targets"))

    @property
    def probe_allowlist_urls(self) -> list[str]:
        return [u.strip().rstrip("/") for u in self.probe_allowlist.split(",") if u.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


_ENV_REF = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _expand(value: Any, settings: Settings) -> Any:
    if isinstance(value, str):

        def repl(m: re.Match[str]) -> str:
            name = m.group(1)
            env = os.environ.get(name)
            if env is not None:
                return env
            return str(getattr(settings, name.lower(), ""))

        return _ENV_REF.sub(repl, value)
    if isinstance(value, dict):
        return {k: _expand(v, settings) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v, settings) for v in value]
    return value


@lru_cache
def load_config(name: str) -> dict[str, Any]:
    """Load config/<name>.yaml with ${ENV} substitution."""
    settings = get_settings()
    path = settings.config_dir / f"{name}.yaml"
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    expanded: dict[str, Any] = _expand(data, settings)
    return expanded
