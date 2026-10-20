"""Validated execution settings, secret references and project readiness."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from core.agent_project import discover_agent
from core.db.models import ExecutionSettings
from core.errors import AppError, ErrorCode
from core.project_probes import uploaded_probe_set
from core.runtime_spec import runtime_spec
from core.secret_store import SecretStore
from core.settings import get_settings

NAME = re.compile(r"^[A-Z_][A-Z0-9_]{0,63}$")
SENSITIVE = re.compile(r"KEY|TOKEN|PASSWORD|SECRET|CREDENTIAL", re.I)


class ExecutionConfig(BaseModel):
    mode: Literal["mock", "live"] = "mock"
    environment: dict[str, dict[str, str]] = Field(default_factory=dict)
    model: str = "gpt-5.4-nano"
    max_requests: int = Field(default=60, ge=1, le=100)
    max_total_tokens: int = Field(default=250_000, ge=1000, le=2_000_000)
    max_output_tokens: int = Field(default=2048, ge=128, le=8192)
    base_url_variable: str = "LLM_BASE_URL"
    api_key_variable: str = "LLM_API_KEY"
    model_variable: str = "LLM_MODEL"
    mode_variable: str = "LLM_MODE"
    live_mode_value: str = "openai"
    mock_mode_value: str = "mock"


class ExecutionUpdate(ExecutionConfig):
    secrets: dict[str, dict[str, SecretStr]] = Field(default_factory=dict)
    provider_api_key: SecretStr | None = None
    remove_secrets: list[str] = Field(default_factory=list)
    remove_provider_key: bool = False


def validate_config(body: ExecutionUpdate) -> None:
    for name in [body.base_url_variable, body.api_key_variable, body.model_variable, body.mode_variable]:
        if not NAME.fullmatch(name):
            raise AppError(ErrorCode.VALIDATION, "환경변수 이름 형식 오류")
    names = [body.base_url_variable, body.api_key_variable, body.model_variable, body.mode_variable]
    if len(set(names)) != 4:
        raise AppError(ErrorCode.VALIDATION, "모델 연결 환경변수 이름은 서로 달라야 합니다.")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", body.model):
        raise AppError(ErrorCode.VALIDATION, "모델 이름 형식 오류")
    if body.model not in get_settings().llm_proxy_model_allowlist.split(","):
        raise AppError(ErrorCode.VALIDATION, "서버에서 허용하지 않은 모델입니다.")
    if len(body.environment) > 3 or len(body.secrets) > 3:
        raise AppError(ErrorCode.VALIDATION, "서비스 설정은 최대 3개입니다.")
    groups: list[tuple[dict[str, Any], bool]] = [(dict(body.environment), False), (dict(body.secrets), True)]
    for group, sensitive in groups:
        for service, values in group.items():
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", service) or len(values) > 32:
                raise AppError(ErrorCode.VALIDATION, "서비스·환경변수 개수 또는 이름 오류")
            for key, value in values.items():
                raw = value.get_secret_value() if isinstance(value, SecretStr) else value
                if not NAME.fullmatch(key) or len(raw) > 4096 or "\x00" in raw:
                    raise AppError(ErrorCode.VALIDATION, "환경변수 이름·값 형식 오류")
                if not sensitive and SENSITIVE.search(key):
                    raise AppError(ErrorCode.VALIDATION, "키·토큰·비밀번호는 비밀값 입력란을 사용하세요.")
                if key in names or key in {"OPENAI_API_KEY", "OPENAI_BASE_URL", "ANTHROPIC_API_KEY"}:
                    raise AppError(ErrorCode.VALIDATION, "LLM 연결 값은 모델 설정과 제공자 키 입력란을 사용하세요.")
                if sensitive and not 4 <= len(raw) <= 4096:
                    raise AppError(ErrorCode.VALIDATION, "비밀값은 4~4096자여야 합니다.")
    if body.mode == "live" and body.max_total_tokens < body.max_output_tokens + 1024:
        raise AppError(ErrorCode.VALIDATION, "총 토큰 한도는 응답 한도보다 최소 1,024 이상 커야 합니다.")
    if body.provider_api_key is not None and not 8 <= len(body.provider_api_key.get_secret_value()) <= 4096:
        raise AppError(ErrorCode.VALIDATION, "제공자 API 키 형식 오류")


def public_settings(row: ExecutionSettings | None) -> dict[str, Any]:
    config = ExecutionConfig.model_validate(row.config if row else {}).model_dump()
    refs = row.secret_refs if row else {}
    return config | {
        "secret_names": sorted(k for k in refs if k != "provider"),
        "provider_key_configured": bool(refs.get("provider")),
        "allowed_models": get_settings().llm_proxy_model_allowlist.split(","),
    }


def environment_for(row: ExecutionSettings | None, *, reveal: bool = False) -> dict[str, dict[str, str]]:
    config = ExecutionConfig.model_validate(row.config if row else {})
    env = {k: dict(v) for k, v in config.environment.items()}
    for name, ref in (row.secret_refs if row else {}).items():
        if name == "provider":
            continue
        service, variable = name.split(":", 1)
        if reveal and row is not None:
            value = SecretStore().get(str(row.target_id) + ":" + name, ref)
        else:
            value = "CONFIGURED_SECRET"
        env.setdefault(service, {})[variable] = value
    for service in ["agent", "target-agent"]:
        target = env.setdefault(service, {})
        target[config.mode_variable] = config.live_mode_value if config.mode == "live" else config.mock_mode_value
        if config.mode == "live":
            target[config.model_variable] = config.model
            target[config.base_url_variable] = "http://audit-llm-relay:9000/v1"
            target[config.api_key_variable] = "AUDIT_TEMPORARY_TOKEN"
    return env


def readiness(root: Path, row: ExecutionSettings | None) -> dict[str, Any]:
    profile = discover_agent(root)
    settings = public_settings(row)
    criteria = [
        {
            "name": "ZIP 입력 검증",
            "met": True,
            "detail": "20MiB / 압축 해제 100MiB / 5,000개 항목, 경로·링크 검사 통과",
        },
        {
            "name": "에이전트 정의",
            "met": bool(profile),
            "detail": "manifest.yaml의 tools 또는 Python 리터럴 TOOL_SCHEMAS/TOOLS가 필요합니다.",
        },
    ]
    try:
        spec = runtime_spec(root, environment_for(row))
        criteria.append(
            {
                "name": "Compose·Dockerfile·환경변수",
                "met": True,
                "detail": "빌드 서비스 1~3개, agent/target-agent와 내부 포트 확인",
            }
        )
    except AppError as exc:
        spec = None
        criteria.append({"name": "Compose·Dockerfile·환경변수", "met": False, "detail": exc.message})
    ps = (
        uploaded_probe_set("preflight", profile["manifest"], profile.get("runtime_protocol", "auditor"))
        if profile
        else None
    )
    criteria.append(
        {
            "name": "공격·통신 계약",
            "met": bool(ps and ps.probes),
            "detail": "Atlas 자동 지원. 그 외 audit-runtime.yaml(protocol: auditor/atlas)과 audit-probes.yaml이 필요합니다.",
        }
    )
    criteria.append(
        {
            "name": "상태·대화 API",
            "met": None,
            "detail": "실행 시 GET /health(200), POST /chat을 확인합니다. Atlas는 POST /sessions도 필요합니다.",
        }
    )
    if settings["mode"] == "live":
        criteria.append(
            {
                "name": "실제 모델 연결 준비",
                "met": settings["provider_key_configured"],
                "detail": "검사용 제공자 키를 비밀 저장소에 저장하고 허용 모델·요청/토큰 한도를 설정하세요.",
            }
        )
    return {
        "criteria": criteria,
        "ready": all(c["met"] is not False for c in criteria),
        "services": [s["name"] for s in spec["services"]] if spec else [],
        "mode": settings["mode"],
        "verification": "실제 모델 호출·공격 결과는 감사 실행 후 확정됩니다.",
    }


async def settings_for_agent(db: AsyncSession, target: Any) -> ExecutionSettings | None:
    return await db.get(ExecutionSettings, target.linked_target_id) if target.linked_target_id else None
