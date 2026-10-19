"""Host 도구 계층 (기획서 6장 MCP 도구 계약·Resource).

모델에 노출되는 실제 도구 이름은 서버 이름에 business_/rag_ 접두어를 붙인 것으로 고정한다.
read_resource 는 MCP tools/list 항목이 아니라 Host 가 제공하는 가상 도구다.
모든 호출: 등록 목록 확인 → 예산(같은 도구 3회) → 백엔드 호출 → 마스킹 → 8000자 상한 → ToolResult.
오류는 예외 대신 ToolResult(error) 로 전달해 모델이 ToolMessage 로 받는다.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from core.cloud.budget import CloudBudget
from core.cloud.masking import Masker
from core.errors import AppError, ErrorCode
from core.settings import load_config


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    mutating: bool = False
    destructive_hint: bool = False


REGISTRY: dict[str, ToolSpec] = {
    t.name: t
    for t in (
        ToolSpec("business_get_audit_evidence", "허용 프로젝트의 구조화 증거(evidence_id 목록 또는 종류)를 조회한다."),
        ToolSpec("business_get_finding_status", "발견사항·티켓 상태를 조회한다."),
        ToolSpec(
            "business_create_remediation_ticket",
            "승인된 수정 요청 티켓을 생성한다. 승인·인수 해시·멱등 키를 서버가 검증한다.",
            mutating=True,
            destructive_hint=True,
        ),
        ToolSpec("rag_search_security_guidance", "기준 문서 청크를 검색한다 (문서 URI·버전 포함)."),
        ToolSpec("read_resource", "허용된 기준 문서 원문 Resource 를 읽는다 (Host 가상 도구)."),
    )
}


class CloudBackend(Protocol):
    async def get_audit_evidence(self, evidence_ids: list[str] | None, kinds: list[str] | None) -> dict[str, Any]: ...
    async def get_finding_status(self, finding_ids: list[str] | None) -> dict[str, Any]: ...
    async def create_remediation_ticket(
        self, task_id: str, action_id: str, args: dict[str, Any], approval_ref: str | None, args_hash: str | None
    ) -> dict[str, Any]: ...
    async def search_guidance(self, query: str, top_k: int) -> list[dict[str, Any]]: ...
    async def read_resource(self, uri: str) -> dict[str, Any]: ...


@dataclass
class ToolResult:
    ok: bool
    tool: str
    content: str
    truncated: bool = False
    error_code: str | None = None
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class ToolLogEntry:
    tool: str
    args_masked: dict[str, Any]
    ok: bool
    error_code: str | None
    elapsed_ms: int
    requested_only: bool = False  # 모델이 요청했으나 시스템이 차단한 경우


class ToolHost:
    def __init__(
        self,
        backend: CloudBackend,
        budget: CloudBudget,
        task_id: str,
        *,
        allowed_resource_uris: set[str],
        masker: Masker | None = None,
        exposed: set[str] | None = None,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.on_event = on_event
        self.backend, self.budget, self.task_id = backend, budget, task_id
        self.allowed_uris = allowed_resource_uris
        self.masker = masker or Masker()
        self.exposed = exposed if exposed is not None else set(REGISTRY)
        self.log: list[ToolLogEntry] = []
        self.max_chars = int(load_config("budget").get("cloud_audit", {}).get("tool_result_max_chars", 8000))

    def specs(self) -> list[ToolSpec]:
        return [REGISTRY[n] for n in sorted(self.exposed)]

    async def call(self, name: str, args: dict[str, Any]) -> ToolResult:
        started = time.monotonic()
        masked_args = self.masker.payload(args)
        self._emit("TOOL_START", {"tool": name, "args": masked_args})
        try:
            if name not in REGISTRY or name not in self.exposed:
                raise AppError(ErrorCode.FORBIDDEN, f"tool {name} is not registered")
            self.budget.start_tool(name)
            data = await self._dispatch(name, args)
        except AppError as e:
            self._log(name, masked_args, False, str(e.code), started, blocked=e.code == ErrorCode.FORBIDDEN)
            self._emit("TOOL_END", {"tool": name, "error": True, "code": str(e.code)})
            return ToolResult(
                False, name, json.dumps({"error": {"code": str(e.code), "message": e.message}}), error_code=str(e.code)
            )
        content = json.dumps(self.masker.payload(data), ensure_ascii=False)
        truncated = len(content) > self.max_chars
        if truncated:
            content = content[: self.max_chars] + "…[truncated]"
        self._log(name, masked_args, True, None, started)
        refs = _refs(data)
        self._emit("TOOL_END", {"tool": name, "error": False, "truncated": truncated, "evidence_refs": refs[:20]})
        return ToolResult(True, name, content, truncated, evidence_refs=refs)

    def _emit(self, type_: str, data: dict[str, Any]) -> None:
        if self.on_event is not None:
            self.on_event(type_, data)

    def _log(
        self, name: str, args: dict[str, Any], ok: bool, code: str | None, started: float, blocked: bool = False
    ) -> None:
        self.log.append(ToolLogEntry(name, args, ok, code, int((time.monotonic() - started) * 1000), blocked))

    async def _dispatch(self, name: str, a: dict[str, Any]) -> Any:
        b = self.backend
        if name == "business_get_audit_evidence":
            return await b.get_audit_evidence(a.get("evidence_ids"), a.get("kinds"))
        if name == "business_get_finding_status":
            return await b.get_finding_status(a.get("finding_ids"))
        if name == "rag_search_security_guidance":
            return {"results": await b.search_guidance(str(a.get("query", "")), int(a.get("top_k", 3)))}
        if name == "read_resource":
            uri = str(a.get("uri", ""))
            if uri not in self.allowed_uris:
                raise AppError(ErrorCode.FORBIDDEN, "resource URI not allowed for this task")
            return await b.read_resource(uri)
        if name == "business_create_remediation_ticket":
            return await b.create_remediation_ticket(
                self.task_id,
                str(a.get("action_id", "")),
                dict(a.get("args", {})),
                a.get("approval_ref"),
                a.get("args_hash"),
            )
        raise AppError(ErrorCode.VALIDATION, f"unknown tool {name}")


def _refs(data: Any) -> list[str]:
    refs: list[str] = []
    if isinstance(data, dict):
        for k, v in data.items():
            if k in ("source_ref", "evidence_id", "uri"):
                refs.append(str(v))
            else:
                refs += _refs(v)
    elif isinstance(data, list):
        for v in data:
            refs += _refs(v)
    return sorted(set(refs))
