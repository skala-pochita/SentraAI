"""business-mcp 변경 도구 (명세 9.3 '변경'). 역할은 서버에서 X-Actor-Role 로 다시 검사한다."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

import yaml
from mcp.server.fastmcp import Context
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from core.db.models import Approval, Finding, FixProposalRow, Report, RiskAcceptance, RiskPath
from core.db.session import session_scope
from core.errors import AppError, ErrorCode
from core.redis.client import get_redis
from core.rules.engine import check_manifest
from core.schemas import FindingDraft, finding_fingerprint
from core.settings import get_settings
from harness.masking import mask_obj, mask_text
from mcp_servers.business.app import mcp
from mcp_servers.business.patches import apply_edits, git_apply, git_apply_check, precheck, unified_diff
from mcp_servers.business.workspace import get_target, git
from mcp_servers.common import current_call, mcp_tool

# Finding 상태 전이 (명세 9.3)
TRANSITIONS: dict[str, set[str]] = {
    "OPEN": {"FIXING", "ACCEPTED_RISK", "FALSE_POSITIVE"},
    "FIXING": {"RESOLVED", "MANUAL_REQUIRED"},
    "RESOLVED": {"OPEN"},
    "ACCEPTED_RISK": set(),
    "FALSE_POSITIVE": set(),
    "MANUAL_REQUIRED": set(),
}


def _uuid(v: str, name: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(v))
    except ValueError as e:
        raise AppError(ErrorCode.VALIDATION, f"{name} must be a UUID") from e


def _actor() -> uuid.UUID:
    call = current_call()
    if not call.actor_id:
        raise AppError(ErrorCode.VALIDATION, "X-Actor-Id header required for mutating tools")
    return _uuid(call.actor_id, "X-Actor-Id")


@mcp_tool(mcp, roles=("ADMIN",))
async def create_finding(audit_id: str, finding: dict[str, Any], ctx: Context | None = None) -> Any:
    """FindingDraft 를 저장한다. fingerprint = hash(target, category, path, normalized_snippet) 중복이면 기존 ID 반환 (멱등).

    source=correlation 이면 component_ids(구성 finding_id 목록)로 risk_paths 도 저장한다.
    입력: audit_id, finding (FindingDraft)
    출력: {finding_id, created}
    권한: ADMIN. 오류: VALIDATION(스키마). 부작용: findings(+risk_paths) 쓰기.
    """
    try:
        f = FindingDraft.model_validate(mask_obj(finding))
    except ValueError as e:
        raise AppError(ErrorCode.VALIDATION, f"invalid FindingDraft: {str(e)[:300]}") from e
    aid = _uuid(audit_id, "audit_id")
    fp = finding_fingerprint(f)
    async with session_scope() as db:
        stmt = (
            insert(Finding)
            .values(
                audit_id=aid,
                target_id=_uuid(f.target_id, "target_id"),
                source=f.source,
                category=f.category,
                rule_id=f.rule_id,
                title=f.title,
                severity=f.severity,
                confidence=f.confidence,
                fingerprint=fp,
                evidence=[e.model_dump() for e in f.evidence],
                reasoning_summary=f.reasoning_summary,
                tools_used=f.tools_used,
                standard_refs=f.standard_refs,
                fix_hint=f.fix_hint,
            )
            .on_conflict_do_nothing(index_elements=["audit_id", "fingerprint"])
            .returning(Finding.id)
        )
        new_id = (await db.execute(stmt)).scalar_one_or_none()
        if new_id is None:
            existing = (
                await db.execute(select(Finding.id).where(Finding.audit_id == aid, Finding.fingerprint == fp))
            ).scalar_one()
            return {"finding_id": str(existing), "created": False}
        if f.source == "correlation":
            comps = [_uuid(c, "component_id") for c in f.component_ids if _is_uuid(c)]
            db.add(
                RiskPath(
                    audit_id=aid,
                    rule_id=f.rule_id or "CORR",
                    severity=f.severity,
                    title=f.title,
                    finding_ids=[new_id, *comps],
                    explanation=f.reasoning_summary,
                )
            )
        return {"finding_id": str(new_id), "created": True}


def _is_uuid(v: str) -> bool:
    try:
        uuid.UUID(v)
        return True
    except ValueError:
        return False


@mcp_tool(mcp, roles=("ADMIN", "DEVELOPER"))
async def update_finding_status(finding_id: str, status: str, reason: str = "", ctx: Context | None = None) -> Any:
    """Finding 상태를 전이한다. 전이 표: OPEN→FIXING→RESOLVED, OPEN→ACCEPTED_RISK, OPEN→FALSE_POSITIVE,
    FIXING→MANUAL_REQUIRED, RESOLVED→OPEN(재발). 같은 상태로의 요청은 그대로 성공(멱등).

    입력: finding_id, status, reason
    출력: {finding_id, status, previous}
    권한: ADMIN, DEVELOPER(FIXING 만 — 대상 소유 검사는 API 계층)
    오류: NOT_FOUND, CONFLICT(전이 표 위반), FORBIDDEN. 부작용: findings.status 변경.
    """
    call = current_call()
    if call.role == "DEVELOPER" and status != "FIXING":
        raise AppError(ErrorCode.FORBIDDEN, "DEVELOPER can only set FIXING")
    async with session_scope() as db:
        f = await db.get(Finding, _uuid(finding_id, "finding_id"))
        if f is None:
            raise AppError(ErrorCode.NOT_FOUND, "finding not found")
        prev = f.status
        if prev != status and status not in TRANSITIONS.get(prev, set()):
            raise AppError(
                ErrorCode.CONFLICT,
                f"invalid transition {prev} → {status}",
                {"allowed": sorted(TRANSITIONS.get(prev, set()))},
            )
        f.status = status
        return {"finding_id": finding_id, "status": status, "previous": prev}


@mcp_tool(mcp, roles=("ADMIN",))
async def request_approval(audit_id: str, finding_ids: list[str], summary: str, ctx: Context | None = None) -> Any:
    """CRITICAL Finding 수정 적용에 대한 보안관리자 승인을 요청한다. 같은 감사·같은 Finding 집합이면 기존 요청 반환(멱등).

    입력: audit_id, finding_ids, summary
    출력: {approval_id, status}
    권한: ADMIN. 오류: VALIDATION. 부작용: approvals 행 생성 (PENDING).
    """
    aid = _uuid(audit_id, "audit_id")
    ids = sorted({_uuid(x, "finding_id") for x in finding_ids})
    if not ids:
        raise AppError(ErrorCode.VALIDATION, "finding_ids required")
    async with session_scope() as db:
        for a in (await db.execute(select(Approval).where(Approval.audit_id == aid))).scalars():
            if sorted(a.finding_ids) == ids:
                return {"approval_id": str(a.id), "status": a.status}
        a = Approval(audit_id=aid, finding_ids=ids, summary=mask_text(summary))
        db.add(a)
        await db.flush()
        return {"approval_id": str(a.id), "status": "PENDING"}


async def _agent_precheck(after: dict[str, str | None], categories: set[str]) -> list[dict[str, Any]]:
    manifest_text = after.get("manifest.yaml")
    prompt = after.get("system_prompt.md")
    if manifest_text is None and prompt is None:
        return []
    m = yaml.safe_load(manifest_text) if manifest_text else {}
    hits = check_manifest(m or {}, manifest_text or "", prompt or "", None)
    return [h.model_dump() for h in hits if h.category in categories]


@mcp_tool(mcp, roles=("ADMIN",))
async def create_fix_proposal(
    audit_id: str,
    finding_ids: list[str],
    kind: Literal["code_patch", "manifest_patch", "prompt_patch"],
    target_id: str,
    diff: str | None = None,
    edits: list[dict[str, Any]] | None = None,
    attempt: int = 1,
    ctx: Context | None = None,
) -> Any:
    """수정안(PR 후보)을 등록한다. diff 또는 edits(서버가 실제 파일에 적용해 diff 계산) 중 하나.
    서버가 git apply --check 를 다시 실행하고, 패치된 파일에 해당 규칙을 재실행해 사전 확인한다.

    입력: audit_id, finding_ids, kind, target_id, diff? | edits?, attempt
    출력: {proposal_id, patch_check_passed, check_error, diff_masked, precheck_remaining: [{rule_id, path, line}]}
    권한: ADMIN. 오류: VALIDATION(edit 불일치), NOT_FOUND. 부작용: fix_proposals 행 생성 (같은 내용이면 기존 반환).
    """
    if not diff and not edits:
        raise AppError(ErrorCode.VALIDATION, "diff or edits required")
    t = await get_target(target_id)
    after: dict[str, str | None] = {}
    if edits:
        before, after = apply_edits(t.root, edits)
        diff = unified_diff(before, after)
    assert diff is not None
    passed, err = await git_apply_check(t.root, diff)
    aid = _uuid(audit_id, "audit_id")
    fids = sorted({_uuid(x, "finding_id") for x in finding_ids})
    remaining: list[dict[str, Any]] = []
    async with session_scope() as db:
        findings = list((await db.execute(select(Finding).where(Finding.id.in_(fids)))).scalars())
        if after:
            if t.type == "repo":
                remaining = [h.model_dump() for h in precheck(after, [f.rule_id for f in findings if f.rule_id])]
            else:
                remaining = await _agent_precheck(after, {f.category for f in findings})
        existing = next(
            (
                p
                for p in (
                    await db.execute(
                        select(FixProposalRow).where(FixProposalRow.audit_id == aid, FixProposalRow.attempt == attempt)
                    )
                ).scalars()
                if p.diff == diff and sorted(p.finding_ids) == fids
            ),
            None,
        )
        if existing is None:
            existing = FixProposalRow(
                audit_id=aid, finding_ids=fids, kind=kind, diff=diff, patch_check_passed=passed, attempt=attempt
            )
            db.add(existing)
            await db.flush()
        return {
            "proposal_id": str(existing.id),
            "target_id": target_id,
            "patch_check_passed": passed,
            "check_error": err or None,
            "diff_masked": mask_text(diff),
            "precheck_remaining": [{"rule_id": h["rule_id"], "path": h["path"], "line": h["line"]} for h in remaining],
        }


async def _proposal_target(db: Any, prop: FixProposalRow) -> str:
    tid = (await db.execute(select(Finding.target_id).where(Finding.id.in_(prop.finding_ids)))).scalars().first()
    if tid is None:
        raise AppError(ErrorCode.NOT_FOUND, "proposal findings not found")
    return str(tid)


def _fault_count() -> int:
    spec = get_settings().fault_injection.strip()
    if not spec.startswith("github_rate_limit:"):
        return 0
    try:
        return int(spec.split(":", 1)[1])
    except ValueError:
        return 0


@mcp_tool(mcp, roles=("ADMIN",))
async def apply_fix(
    proposal_id: str, idempotency_key: str, approval_id: str | None = None, ctx: Context | None = None
) -> Any:
    """승인된 수정안으로 브랜치·PR 을 만든다. 자동 병합하지 않는다.

    CRITICAL Finding 이 포함되면 approval_id 의 상태가 APPROVED 여야 하며 아니면 FORBIDDEN.
    같은 idempotency_key 재호출은 기존 결과를 반환한다 (PR 1개).
    GIT_MODE=local: 작업공간 git 에 fix/* 브랜치 커밋, PR = fix_proposals 레코드 + diff.
    입력: proposal_id, idempotency_key, approval_id?
    출력: {branch, pr_url, reused}
    권한: ADMIN. 오류: FORBIDDEN(승인 없음), CONFLICT(패치 적용 실패), RATE_LIMITED(외부 한도·장애 주입), NOT_FOUND.
    부작용: git 브랜치·커밋 생성, fix_proposals 갱신.
    """
    pid = _uuid(proposal_id, "proposal_id")
    async with session_scope() as db:
        prop = await db.get(FixProposalRow, pid)
        if prop is None:
            raise AppError(ErrorCode.NOT_FOUND, "proposal not found")
        if prop.branch and prop.idempotency_key == idempotency_key:
            return {"branch": prop.branch, "pr_url": prop.pr_url, "reused": True}
        other = (
            await db.execute(select(FixProposalRow).where(FixProposalRow.idempotency_key == idempotency_key))
        ).scalar_one_or_none()
        if other is not None and other.id != pid:
            raise AppError(ErrorCode.CONFLICT, "idempotency_key already used by another proposal")
        crit = (
            await db.execute(select(Finding.id).where(Finding.id.in_(prop.finding_ids), Finding.severity == "CRITICAL"))
        ).first()
        if crit is not None:
            if not approval_id:
                raise AppError(ErrorCode.FORBIDDEN, "CRITICAL fix requires an APPROVED approval_id")
            appr = await db.get(Approval, _uuid(approval_id, "approval_id"))
            if appr is None or appr.status != "APPROVED" or appr.audit_id != prop.audit_id:
                raise AppError(
                    ErrorCode.FORBIDDEN,
                    "approval is not APPROVED for this audit",
                    {"approval_status": appr.status if appr else None},
                )
        if not prop.patch_check_passed:
            raise AppError(ErrorCode.CONFLICT, "patch check failed; cannot apply")
        diff, audit_id = prop.diff, prop.audit_id
        target_id = await _proposal_target(db, prop)

    if (n := _fault_count()) > 0:  # 장애 주입: 처음 n 회 RATE_LIMITED
        r = get_redis()
        key = f"fault:apply_fix:{proposal_id}"
        count = await r.incr(key)
        await r.expire(key, 3600)
        if count <= n:
            raise AppError(
                ErrorCode.RATE_LIMITED, "GitHub API rate limit (fault injection)", {"attempt": count}, retry_after_s=1
            )

    t = await get_target(target_id)
    if get_settings().git_mode == "github":
        raise AppError(ErrorCode.VALIDATION, "GIT_MODE=github is not implemented in MVP (Phase 9)")
    branch = f"fix/{str(audit_id)[:8]}-{proposal_id[:8]}"
    await git(t.root, "checkout", "-q", "main")
    exists = (await git(t.root, "rev-parse", "--verify", "--quiet", branch, check=False)).returncode == 0
    if not exists:
        await git(t.root, "checkout", "-q", "-b", branch)
        try:
            await git_apply(t.root, diff)
            await git(t.root, "commit", "-q", "-m", f"fix: audit {str(audit_id)[:8]} proposal {proposal_id[:8]}")
        finally:
            await git(t.root, "checkout", "-q", "-f", "main")
    pr_url = f"local://{t.name}/{branch}"
    async with session_scope() as db:
        prop = await db.get(FixProposalRow, pid)
        assert prop is not None
        prop.branch, prop.pr_url, prop.idempotency_key = branch, pr_url, idempotency_key
        if approval_id:
            prop.approval_id = _uuid(approval_id, "approval_id")
    return {"branch": branch, "pr_url": pr_url, "reused": False}


@mcp_tool(mcp, roles=("ADMIN", "DEVELOPER"))
async def merge_fix(proposal_id: str, ctx: Context | None = None) -> Any:
    """GIT_MODE=local 병합 시뮬레이션: fix 브랜치를 main 에 merge 한다 (대시보드 Merge 버튼).

    입력: proposal_id
    출력: {merged: true, commit_sha}
    권한: ADMIN, DEVELOPER(자기 대상 — API 계층 검사). 오류: NOT_FOUND, CONFLICT(브랜치 없음·충돌).
    부작용: main 브랜치 갱신, fix_proposals.merge_status=MERGED.
    """
    pid = _uuid(proposal_id, "proposal_id")
    async with session_scope() as db:
        prop = await db.get(FixProposalRow, pid)
        if prop is None:
            raise AppError(ErrorCode.NOT_FOUND, "proposal not found")
        if not prop.branch:
            raise AppError(ErrorCode.CONFLICT, "proposal has no branch (apply_fix first)")
        target_id = await _proposal_target(db, prop)
        if prop.merge_status == "MERGED":
            t = await get_target(target_id)
            sha = (await git(t.root, "rev-parse", "HEAD")).stdout.strip()
            return {"merged": True, "commit_sha": sha, "reused": True}
        branch = prop.branch
    t = await get_target(target_id)
    await git(t.root, "checkout", "-q", "main")
    await git(t.root, "merge", "-q", "--no-ff", "-m", f"Merge {branch}", branch)
    sha = (await git(t.root, "rev-parse", "HEAD")).stdout.strip()
    async with session_scope() as db:
        prop = await db.get(FixProposalRow, pid)
        assert prop is not None
        prop.merge_status = "MERGED"
    return {"merged": True, "commit_sha": sha, "reused": False}


@mcp_tool(mcp, roles=("ADMIN",))
async def register_risk_acceptance(finding_id: str, reason: str, expires_at: str, ctx: Context | None = None) -> Any:
    """반려된 Finding 을 위험 수용으로 등록한다 (사유, 만료일). Finding 상태 → ACCEPTED_RISK.

    입력: finding_id, reason, expires_at (ISO8601)
    출력: {acceptance_id}
    권한: ADMIN. 오류: VALIDATION, NOT_FOUND, CONFLICT(상태 전이). 부작용: risk_acceptances 생성, 상태 변경.
    """
    try:
        exp = datetime.fromisoformat(expires_at)
    except ValueError as e:
        raise AppError(ErrorCode.VALIDATION, "expires_at must be ISO8601") from e
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=UTC)
    actor = _actor()
    async with session_scope() as db:
        f = await db.get(Finding, _uuid(finding_id, "finding_id"))
        if f is None:
            raise AppError(ErrorCode.NOT_FOUND, "finding not found")
        existing = (
            await db.execute(select(RiskAcceptance).where(RiskAcceptance.finding_id == f.id))
        ).scalar_one_or_none()
        if existing is not None:
            return {"acceptance_id": str(existing.id)}
        if f.status != "OPEN" and f.status != "ACCEPTED_RISK":
            raise AppError(ErrorCode.CONFLICT, f"cannot accept risk for {f.status} finding")
        ra = RiskAcceptance(finding_id=f.id, reason=mask_text(reason), accepted_by=actor, expires_at=exp)
        db.add(ra)
        f.status = "ACCEPTED_RISK"
        await db.flush()
        return {"acceptance_id": str(ra.id)}


@mcp_tool(mcp, roles=("ADMIN",))
async def save_report(audit_id: str, report_json: dict[str, Any], report_md: str, ctx: Context | None = None) -> Any:
    """감사 보고서를 저장한다 (감사당 1개, 재호출 시 갱신). 저장 전 비밀값을 다시 마스킹한다.

    입력: audit_id, report_json, report_md
    출력: {report_id}
    권한: ADMIN. 오류: VALIDATION. 부작용: reports upsert.
    """
    aid = _uuid(audit_id, "audit_id")
    body_json = mask_obj(report_json)
    body_md = mask_text(report_md)
    async with session_scope() as db:
        stmt = (
            insert(Report)
            .values(audit_id=aid, report_json=body_json, report_md=body_md)
            .on_conflict_do_update(index_elements=["audit_id"], set_={"report_json": body_json, "report_md": body_md})
            .returning(Report.id)
        )
        rid = (await db.execute(stmt)).scalar_one()
        return {"report_id": str(rid)}
