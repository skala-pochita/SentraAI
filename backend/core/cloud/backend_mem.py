"""서버 측 모의 저장소 (평가 실행기·단위 테스트). 모델이 평가 모드를 켜거나 저장소를 고를 수 없다.

변경 도구도 승인 경계를 그대로 거친다 (TicketStore). 제품 경로는 business-mcp/rag-mcp 가 같은 계약을 구현한다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from core.cloud.bundle import EvidenceBundle
from core.cloud.tickets import TicketStore
from core.errors import AppError, ErrorCode
from core.settings import get_settings

GUIDANCE_URI = "guidance://security-baseline/v1"
GUIDANCE_FILE = "cloud_security_baseline.md"


def _guidance_path() -> Path:
    return get_settings().knowledge_dir / GUIDANCE_FILE


class MemoryBackend:
    def __init__(self, bundle: EvidenceBundle, tickets: TicketStore | None = None) -> None:
        self.bundle = bundle
        self.tickets = tickets or TicketStore()

    # ---- 증거 ----
    def _evidence_index(self) -> dict[str, dict[str, Any]]:
        b = self.bundle
        idx: dict[str, dict[str, Any]] = {}
        for kind, items in (
            ("tool", b.tools),
            ("principal", b.principals),
            ("policy", b.policies),
            ("resource", b.resources),
        ):
            for o in items:
                idx[o.id] = {"kind": kind, "evidence_id": o.id, "source_ref": o.source_ref, **o.model_dump()}
        for m in b.mappings.tool_to_principal:
            idx[f"map:{m.tool_id}->{m.principal_id}"] = {
                "kind": "mapping",
                "evidence_id": f"map:{m.tool_id}->{m.principal_id}",
                **m.model_dump(),
            }
        return idx

    async def get_audit_evidence(self, evidence_ids: list[str] | None, kinds: list[str] | None) -> dict[str, Any]:
        idx = self._evidence_index()
        if evidence_ids:
            missing = [i for i in evidence_ids if i not in idx]
            items = [idx[i] for i in evidence_ids if i in idx]
        else:
            missing = []
            items = [v for v in idx.values() if not kinds or v["kind"] in kinds]
        return {
            "bundle_id": self.bundle.bundle_id,
            "items": items,
            "not_found": missing,
            "business_policy": self.bundle.business_policy.model_dump(),
            "missing_evidence": [m.model_dump() for m in self.bundle.missing_evidence],
        }

    async def get_finding_status(self, finding_ids: list[str] | None) -> dict[str, Any]:
        return {"findings": [], "tickets": [t for t in self.tickets.tickets.values()]}

    async def create_remediation_ticket(
        self, task_id: str, action_id: str, args: dict[str, Any], approval_ref: str | None, args_hash: str | None
    ) -> dict[str, Any]:
        return self.tickets.create(task_id, action_id, args, approval_ref, args_hash)

    # ---- 기준 문서 ----
    def _chunks(self) -> list[dict[str, str]]:
        text = _guidance_path().read_text(encoding="utf-8")
        parts = re.split(r"(?m)^## ", text)
        chunks = []
        for i, part in enumerate(parts[1:], start=1):
            title, _, body = part.partition("\n")
            chunks.append(
                {
                    "doc_id": "security-baseline",
                    "chunk_id": f"security-baseline#{i}",
                    "title": title.strip(),
                    "text": body.strip(),
                    "uri": GUIDANCE_URI,
                    "version": "v1",
                }
            )
        return chunks

    async def search_guidance(self, query: str, top_k: int) -> list[dict[str, Any]]:
        terms = {t for t in re.findall(r"[\w가-힣*]+", query.lower()) if len(t) > 1}
        scored = []
        for c in self._chunks():
            hay = (c["title"] + " " + c["text"]).lower()
            score = sum(1 for t in terms if t in hay)
            if score:
                scored.append((score, c))
        scored.sort(key=lambda x: -x[0])
        return [c for _, c in scored[: max(1, min(top_k, 5))]]

    async def read_resource(self, uri: str) -> dict[str, Any]:
        if uri != GUIDANCE_URI:
            raise AppError(ErrorCode.NOT_FOUND, "resource not found")
        return {"uri": GUIDANCE_URI, "version": "v1", "text": _guidance_path().read_text(encoding="utf-8")}
