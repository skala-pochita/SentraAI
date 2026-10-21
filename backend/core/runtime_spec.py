"""Validate project launch metadata; never run uploaded Compose instructions directly."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from core.errors import AppError, ErrorCode


def inside(root: Path, value: str) -> Path:
    path = (root / value).resolve()
    if not path.is_relative_to(root.resolve()) or not path.exists():
        raise AppError(ErrorCode.VALIDATION, "실행 설정 경로가 프로젝트를 벗어나거나 존재하지 않습니다.")
    return path


COMPOSE_NAMES = ("compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml")
# 격리 실행에서 받지 않는 Compose 설정. security_opt 는 상속하지 않으므로 표준 no-new-privileges 입력은 무해해 허용한다.
FORBIDDEN_KEYS = {
    "privileged",
    "devices",
    "network_mode",
    "pid",
    "ipc",
    "cap_add",
    "env_file",
    "volumes_from",
    "entrypoint",
    "command",
}
SECRET_WORDS = ("KEY", "TOKEN", "PASSWORD", "SECRET")


def _fail(message: str) -> AppError:
    return AppError(ErrorCode.VALIDATION, message)


def _compose_services(root: Path) -> dict[str, Any]:
    compose = next((root / n for n in COMPOSE_NAMES if (root / n).is_file()), None)
    if compose is None:
        raise _fail("자동 실행에는 프로젝트 루트의 Compose 파일과 Dockerfile이 필요합니다.")
    try:
        data = yaml.safe_load(compose.read_text())
    except yaml.YAMLError as exc:
        raise _fail("Compose YAML을 해석할 수 없습니다.") from exc
    services = data.get("services") if isinstance(data, dict) else None
    if not isinstance(services, dict) or not 1 <= len(services) <= 3:
        raise _fail("자동 실행은 빌드 서비스 1~3개를 지원합니다.")
    return services


def _check_service(name: Any, cfg: Any) -> None:
    if not isinstance(name, str) or not name.replace("-", "").replace("_", "").isalnum() or not isinstance(cfg, dict):
        raise _fail("서비스 이름·설정 형식 오류")
    if any(cfg.get(k) for k in FORBIDDEN_KEYS):
        raise _fail(f"{name}: 격리 실행에서 지원하지 않는 설정")


def _build_paths(root: Path, cfg: dict[str, Any]) -> tuple[Path, Path]:
    build = cfg.get("build")
    if isinstance(build, str):
        build = {"context": build}
    if not isinstance(build, dict) or set(build) - {"context", "dockerfile"}:
        raise _fail("로컬 Dockerfile 빌드만 지원합니다. 빌드 인자·비밀값·원격 이미지는 허용하지 않습니다.")
    context = inside(root, str(build.get("context", ".")))
    return context, inside(context, str(build.get("dockerfile", "Dockerfile")))


def _environment(name: str, raw_env: Any, extra: dict[str, str]) -> dict[str, str]:
    """리터럴 값만 받는다. ${VAR} 와 비밀처럼 보이는 이름은 실행 설정(extra)으로 채워야 한다."""
    if not isinstance(raw_env, dict):
        raise _fail("environment는 이름: 값 형식이어야 합니다.")
    env = {}
    for key, value in raw_env.items():
        if key in extra:
            env[key] = extra[key]
            continue
        if not isinstance(value, (str, int, bool)) or "${" in str(value):
            raise _fail(f"{name}.{key}: 실행 설정에서 값을 입력하세요.")
        if any(word in str(key).upper() for word in SECRET_WORDS):
            raise _fail(f"{name}.{key}: ZIP 대신 비밀값 설정으로 등록하세요.")
        env[key] = str(value)
    return env | extra


def _fixture_copy(root: Path, context: Path, mount: Any) -> dict[str, str]:
    if not isinstance(mount, str) or mount.count(":") != 2:
        raise _fail("상대경로 읽기 전용 fixture 마운트만 지원합니다.")
    src, dst, access = mount.split(":")
    if not src.startswith("./") or access != "ro" or not dst.startswith("/lab/fixtures") or ".." in Path(dst).parts:
        raise _fail("fixture는 /lab/fixtures 하위 읽기 전용 경로만 지원합니다.")
    source = inside(root, src)
    if not source.is_relative_to(context):
        raise _fail("fixture는 빌드 컨텍스트 안에 있어야 합니다.")
    return {"source": source.relative_to(context).as_posix(), "destination": dst}


def _port(name: str, cfg: dict[str, Any]) -> int:
    ports = cfg.get("ports", []) or cfg.get("expose", [])
    if not ports:
        raise _fail(f"{name}: 내부 포트를 ports 또는 expose로 명시하세요.")
    try:
        port = int(str(ports[0]).split(":")[-1].split("/")[0])
    except (ValueError, TypeError) as exc:
        raise _fail("포트 형식 오류") from exc
    if not 1 <= port <= 65535:
        raise _fail("포트 범위 오류")
    return port


def _service_spec(root: Path, name: Any, cfg: Any, overrides: dict[str, dict[str, str]]) -> dict[str, Any]:
    _check_service(name, cfg)
    context, dockerfile = _build_paths(root, cfg)
    env = _environment(name, cfg.get("environment", {}), overrides.get(name, {}))
    copies = [_fixture_copy(root, context, m) for m in cfg.get("volumes", [])]
    return {
        "name": name,
        "context": context.relative_to(root).as_posix(),
        "dockerfile": dockerfile.relative_to(context).as_posix(),
        "environment": env,
        "fixture_copies": copies,
        "port": _port(name, cfg),
    }


def runtime_spec(root: Path, overrides: dict[str, dict[str, str]] | None = None) -> dict[str, Any]:
    root = root.resolve()
    specs = [_service_spec(root, name, cfg, overrides or {}) for name, cfg in _compose_services(root).items()]
    agent = next((s for s in specs if s["name"] in ("agent", "target-agent")), None)
    if agent is None:
        raise _fail("agent 또는 target-agent 서비스를 명시하세요.")
    return {"services": specs, "agent_service": agent["name"], "agent_port": agent["port"]}
