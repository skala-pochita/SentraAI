"""Service-only sandbox controller client; never sends auditor/provider credentials to targets."""

from typing import Any
from urllib.parse import urlsplit

import httpx

from core.errors import AppError, ErrorCode
from core.settings import get_settings


async def sandbox_call(path: str, payload: dict[str, Any] | None = None, timeout: float = 60) -> dict[str, Any]:
    settings = get_settings()
    parsed = urlsplit(settings.sandbox_url)
    if parsed.scheme != "https" and parsed.hostname not in {"sandbox-runner", "localhost", "127.0.0.1"}:
        raise AppError(ErrorCode.VALIDATION, "원격 실행 서버에는 HTTPS가 필요합니다.")
    proxy_url = urlsplit(settings.llm_proxy_url)
    if proxy_url.scheme != "https" and proxy_url.hostname not in {"llm-proxy", "localhost", "127.0.0.1"}:
        raise AppError(ErrorCode.VALIDATION, "원격 모델 프록시에는 HTTPS가 필요합니다.")
    if path.startswith("/stop/"):
        from core.proxy_sessions import revoke

        await revoke(path.rsplit("/", 1)[1])
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await client.post(
                settings.sandbox_url + path,
                json=payload or {},
                headers={"Authorization": "Bearer " + (settings.sandbox_service_token or settings.mcp_service_token)},
            )
        if not response.is_success:
            # 422 는 프로젝트를 빌드·기동할 수 없다는 뜻이라 다시 해도 같다 → 재시도하지 않는다.
            # 409(다른 감사가 실행 중)·5xx 는 일시적일 수 있어 재시도 대상으로 둔다
            raise AppError(
                ErrorCode.VALIDATION if response.status_code == 422 else ErrorCode.UPSTREAM_ERROR,
                _failure_message(response)[:450],
            )
        return response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise AppError(ErrorCode.UPSTREAM_ERROR, "격리 실행 서비스 연결 또는 응답 오류") from exc


def _failure_message(response: httpx.Response) -> str:
    """sandbox-runner 오류 본문 {"error": {"message"}} 의 사유. 본문이 없으면 상태 코드."""
    try:
        body = response.json()
    except ValueError:
        body = None
    error = body.get("error") if isinstance(body, dict) else None
    message = str(error.get("message") or "") if isinstance(error, dict) else ""
    if not message:
        return f"격리 실행 실패: HTTP {response.status_code}"
    return message if message.startswith("격리 실행") else "격리 실행 실패: " + message
