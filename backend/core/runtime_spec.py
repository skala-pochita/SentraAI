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


def runtime_spec(root: Path, overrides: dict[str, dict[str, str]] | None = None) -> dict[str, Any]:
    root = root.resolve()
    compose = next(
        (
            root / n
            for n in ("compose.yaml", "compose.yml", "docker-compose.yml", "docker-compose.yaml")
            if (root / n).is_file()
        ),
        None,
    )
    if compose is None:
        raise AppError(ErrorCode.VALIDATION, "자동 실행에는 프로젝트 루트의 Compose 파일과 Dockerfile이 필요합니다.")
    try:
        data = yaml.safe_load(compose.read_text())
    except yaml.YAMLError as exc:
        raise AppError(ErrorCode.VALIDATION, "Compose YAML을 해석할 수 없습니다.") from exc
    services = data.get("services") if isinstance(data, dict) else None
    if not isinstance(services, dict) or not 1 <= len(services) <= 3:
        raise AppError(ErrorCode.VALIDATION, "자동 실행은 빌드 서비스 1~3개를 지원합니다.")
    specs = []
    for name, cfg in services.items():
        if (
            not isinstance(name, str)
            or not name.replace("-", "").replace("_", "").isalnum()
            or not isinstance(cfg, dict)
        ):
            raise AppError(ErrorCode.VALIDATION, "서비스 이름·설정 형식 오류")
        forbidden = {
            "privileged",
            "devices",
            "network_mode",
            "pid",
            "ipc",
            "cap_add",
            "security_opt",
            "env_file",
            "volumes_from",
            "entrypoint",
            "command",
        }
        # security_opt is never inherited; standard no-new-privileges input is harmless.
        forbidden.discard("security_opt")
        if any(cfg.get(k) for k in forbidden):
            raise AppError(ErrorCode.VALIDATION, f"{name}: 격리 실행에서 지원하지 않는 설정")
        build = cfg.get("build")
        if isinstance(build, str):
            build = {"context": build}
        if not isinstance(build, dict) or set(build) - {"context", "dockerfile"}:
            raise AppError(
                ErrorCode.VALIDATION,
                "로컬 Dockerfile 빌드만 지원합니다. 빌드 인자·비밀값·원격 이미지는 허용하지 않습니다.",
            )
        context = inside(root, str(build.get("context", ".")))
        dockerfile = inside(context, str(build.get("dockerfile", "Dockerfile")))
        raw_env = cfg.get("environment", {})
        if not isinstance(raw_env, dict):
            raise AppError(ErrorCode.VALIDATION, "environment는 이름: 값 형식이어야 합니다.")
        extra = (overrides or {}).get(name, {})
        env = {}
        for key, value in raw_env.items():
            if key in extra:
                env[key] = extra[key]
                continue
            if not isinstance(value, (str, int, bool)) or "${" in str(value):
                raise AppError(ErrorCode.VALIDATION, f"{name}.{key}: 실행 설정에서 값을 입력하세요.")
            if any(word in str(key).upper() for word in ("KEY", "TOKEN", "PASSWORD", "SECRET")):
                raise AppError(ErrorCode.VALIDATION, f"{name}.{key}: ZIP 대신 비밀값 설정으로 등록하세요.")
            env[key] = str(value)
        env.update(extra)
        copies = []
        for mount in cfg.get("volumes", []):
            if not isinstance(mount, str) or mount.count(":") != 2:
                raise AppError(ErrorCode.VALIDATION, "상대경로 읽기 전용 fixture 마운트만 지원합니다.")
            src, dst, access = mount.split(":")
            if (
                not src.startswith("./")
                or access != "ro"
                or not dst.startswith("/lab/fixtures")
                or ".." in Path(dst).parts
            ):
                raise AppError(ErrorCode.VALIDATION, "fixture는 /lab/fixtures 하위 읽기 전용 경로만 지원합니다.")
            source = inside(root, src)
            if not source.is_relative_to(context):
                raise AppError(ErrorCode.VALIDATION, "fixture는 빌드 컨텍스트 안에 있어야 합니다.")
            copies.append({"source": source.relative_to(context).as_posix(), "destination": dst})
        ports = cfg.get("ports", []) or cfg.get("expose", [])
        if not ports:
            raise AppError(ErrorCode.VALIDATION, f"{name}: 내부 포트를 ports 또는 expose로 명시하세요.")
        try:
            port = int(str(ports[0]).split(":")[-1].split("/")[0])
        except (ValueError, TypeError) as exc:
            raise AppError(ErrorCode.VALIDATION, "포트 형식 오류") from exc
        if not 1 <= port <= 65535:
            raise AppError(ErrorCode.VALIDATION, "포트 범위 오류")
        specs.append(
            {
                "name": name,
                "context": context.relative_to(root).as_posix(),
                "dockerfile": dockerfile.relative_to(context).as_posix(),
                "environment": env,
                "fixture_copies": copies,
                "port": port,
            }
        )
    agent = next((s for s in specs if s["name"] in ("agent", "target-agent")), None)
    if agent is None:
        raise AppError(ErrorCode.VALIDATION, "agent 또는 target-agent 서비스를 명시하세요.")
    return {"services": specs, "agent_service": agent["name"], "agent_port": agent["port"]}
