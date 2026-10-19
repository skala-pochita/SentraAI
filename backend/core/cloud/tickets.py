"""수정 요청 티켓 (기획서 6장 MCP 도구 계약): 승인·인수 해시·멱등 키 검증.

- 인수는 공통 JSON 정규화(키 정렬, 공백 없음, UTF-8) 후 SHA-256.
- 승인(approval_ref)은 같은 task_id·action_id·args_hash 로 APPROVED 여야 한다. 모델이 만든 승인은 무효.
- (task_id, action_id) 유일: 같은 인수 재시도 → 기존 결과, 다른 인수 → CONFLICT.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from core.errors import AppError, ErrorCode


def canonical_args(args: dict[str, Any]) -> str:
    return json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def args_hash(args: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_args(args).encode("utf-8")).hexdigest()


@dataclass
class ApprovalRecord:
    approval_ref: str
    task_id: str
    action_id: str
    args_hash: str
    decision: str  # APPROVED | REJECTED


@dataclass
class TicketStore:
    """서버 측 저장소 (평가 모의 저장소 / 단위 테스트). 제품은 같은 규칙을 DB 위에서 수행한다."""

    approvals: dict[str, ApprovalRecord] = field(default_factory=dict)
    tickets: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)

    def record_approval(self, rec: ApprovalRecord) -> None:
        self.approvals[rec.approval_ref] = rec

    def create(
        self,
        task_id: str,
        action_id: str,
        args: dict[str, Any],
        approval_ref: str | None,
        claimed_hash: str | None = None,
    ) -> dict[str, Any]:
        h = args_hash(args)
        existing = self.tickets.get((task_id, action_id))
        if existing is not None:
            if existing["args_hash"] != h:
                raise AppError(ErrorCode.CONFLICT, "same actionId with different arguments")
            return {**existing, "duplicate": True}
        rec = self.approvals.get(approval_ref or "")
        if rec is None or rec.decision != "APPROVED":
            raise AppError(ErrorCode.FORBIDDEN, "no valid approval for this change")
        if rec.task_id != task_id or rec.action_id != action_id:
            raise AppError(ErrorCode.FORBIDDEN, "approval does not belong to this action")
        if rec.args_hash != h or (claimed_hash is not None and claimed_hash != h):
            raise AppError(ErrorCode.FORBIDDEN, "arguments differ from the approved ones")
        ticket = {
            "ticket_id": f"TCK-{len(self.tickets) + 1:04d}",
            "task_id": task_id,
            "action_id": action_id,
            "args_hash": h,
            "title": args.get("title"),
            "target": args.get("target"),
        }
        self.tickets[(task_id, action_id)] = ticket
        return {**ticket, "duplicate": False}
