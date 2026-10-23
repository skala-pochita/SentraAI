"""Validate persisted evidence against its audit and target boundaries."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from core.db.models import AuditTarget, Finding, ProbeRun
from core.errors import AppError, ErrorCode
from core.path_schema import AttackPath


def _id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise AppError(ErrorCode.VALIDATION, "invalid evidence reference ID") from exc


async def validate_path(db: Any, audit_id: uuid.UUID, path: AttackPath) -> None:
    targets = set((await db.execute(select(AuditTarget.target_id).where(AuditTarget.audit_id == audit_id))).scalars())
    if not {_id(t) for t in path.target_ids} <= targets:
        raise AppError(ErrorCode.VALIDATION, "path target outside audit")
    refs = [r for item in [*path.steps, *path.edges] for r in item.evidence_refs] + [
        reason.ref for reason in path.code_reasons
    ]
    for ref in refs:
        if ref.finding_id:
            finding = await db.get(Finding, _id(ref.finding_id))
            if finding is None or finding.audit_id != audit_id or str(finding.target_id) != ref.target_id:
                raise AppError(ErrorCode.VALIDATION, "finding reference outside audit/target")
            if ref.evidence_index is not None:
                if ref.evidence_index >= len(finding.evidence):
                    raise AppError(ErrorCode.VALIDATION, "evidence reference out of range")
                ev = finding.evidence[ref.evidence_index]
                if ev.get("file_path") != ref.file_path or ev.get("line_start") != ref.line_start:
                    raise AppError(ErrorCode.VALIDATION, "evidence reference location mismatch")
                for reason in path.code_reasons:
                    if reason.ref == ref:
                        if reason.snippet_masked != ev.get("snippet_masked"):
                            raise AppError(ErrorCode.VALIDATION, "code reason differs from saved evidence")
                        expected_kind = (
                            "policy"
                            if ev.get("kind") == "manifest"
                            else (
                                "implementation_candidate"
                                if ev.get("relation") in {"tool_dispatch_candidate", "tool_function_candidate"}
                                else "code_finding"
                            )
                        )
                        if reason.kind != expected_kind:
                            raise AppError(ErrorCode.VALIDATION, "code reason misrepresents evidence kind")
            if ref.file_path and not any(
                e.get("file_path") == ref.file_path
                and (ref.line_start is None or e.get("line_start") == ref.line_start)
                for e in finding.evidence
            ):
                raise AppError(ErrorCode.VALIDATION, "code location not present in finding evidence")
        if ref.probe_run_id:
            run = await db.get(ProbeRun, _id(ref.probe_run_id))
            if run is None or run.audit_id != audit_id or str(run.target_id) != ref.target_id:
                raise AppError(ErrorCode.VALIDATION, "probe reference outside audit/target")
            if ref.tool_call_index is not None:
                if ref.tool_call_index >= len(run.tool_calls):
                    raise AppError(ErrorCode.VALIDATION, "tool call reference out of range")
                if run.tool_calls[ref.tool_call_index].get("executed") is not True:
                    raise AppError(ErrorCode.VALIDATION, "referenced tool call was not executed")
