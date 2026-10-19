"""business-mcp 조회 도구 (명세 9.3 '조회')."""

from __future__ import annotations

import asyncio
import re
import uuid
from typing import Any, Literal

import yaml
from mcp.server.fastmcp import Context
from sqlalchemy import select

from core.db.models import Audit, AuditTarget, Finding
from core.db.session import session_scope
from core.errors import AppError, ErrorCode
from core.rules.engine import glob_match, iter_repo_files, read_text
from harness.masking import mask_text
from mcp_servers.business.app import mcp
from mcp_servers.business.workspace import commit_sha, get_target, list_targets, safe_path
from mcp_servers.common import mcp_tool

MAX_READ_LINES = 400
SEARCH_TIMEOUT_S = 1.0


@mcp_tool(mcp)
async def list_audit_targets(type: Literal["repo", "agent"] | None = None, ctx: Context | None = None) -> Any:
    """감사 대상 목록을 반환한다.

    입력: type (선택, "repo" | "agent")
    출력: [{target_id, type, name, ref, linked_target_id, commit_sha?}]
    오류: 없음 (빈 목록 가능). 부작용: 없음 (읽기 전용).
    """
    out = []
    for t in await list_targets(type):
        item: dict[str, Any] = {"target_id": t.id, "type": t.type, "name": t.name, "ref": t.ref,
                                "linked_target_id": t.linked_target_id}
        if t.type == "repo":
            item["commit_sha"] = await commit_sha(t.root)
        out.append(item)
    return out


def _lang(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return {"java": "java", "kt": "kotlin", "yml": "yaml", "yaml": "yaml", "md": "markdown",
            "properties": "properties", "gradle": "gradle", "pem": "pem", "py": "python"}.get(ext, ext or "text")


@mcp_tool(mcp)
async def list_repo_files(target_id: str, glob: str | None = None, ctx: Context | None = None) -> Any:
    """레포 대상의 파일 목록 (바이너리·.git·빌드 산출물 제외).

    입력: target_id, glob (선택, 예: "**/*Config*.java")
    출력: [{path, size, lang}]
    오류: NOT_FOUND(대상 없음), VALIDATION(agent 대상). 부작용: 없음.
    """
    t = await get_target(target_id, "repo")
    files = []
    for rel, p in iter_repo_files(t.root):
        if glob and not glob_match(rel, [glob]):
            continue
        if read_text(p) is None:
            continue
        files.append({"path": rel, "size": p.stat().st_size, "lang": _lang(rel)})
    return files


@mcp_tool(mcp)
async def read_repo_file(target_id: str, path: str, start_line: int | None = None, end_line: int | None = None,
                         ctx: Context | None = None) -> Any:
    """레포 파일의 라인 범위를 읽는다. 최대 400줄, 비밀값은 마스킹된다.

    입력: target_id, path (레포 루트 기준 상대경로), start_line/end_line (1-based, 선택)
    출력: {path, start_line, end_line, content_masked, total_lines}
    오류: VALIDATION(절대경로·'..'·.git 등 경로 탈출), NOT_FOUND(파일 없음). 부작용: 없음.
    """
    t = await get_target(target_id, "repo")
    full = safe_path(t.root, path)
    if not full.is_file():
        raise AppError(ErrorCode.NOT_FOUND, f"file not found: {path}")
    text = read_text(full)
    if text is None:
        raise AppError(ErrorCode.VALIDATION, "binary file")
    lines = text.splitlines()
    total = len(lines)
    s = max(1, start_line or 1)
    e = min(total, end_line or (s + MAX_READ_LINES - 1), s + MAX_READ_LINES - 1)
    content = "\n".join(lines[s - 1 : e]) if total else ""
    return {"path": path, "start_line": s, "end_line": e, "content_masked": mask_text(content), "total_lines": total}


def _search_python(root: Any, pattern: re.Pattern[str], glob: str | None, limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rel, p in iter_repo_files(root):
        if glob and not glob_match(rel, [glob]):
            continue
        text = read_text(p)
        if text is None:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                out.append({"path": rel, "line": i, "preview_masked": mask_text(line.strip())[:200]})
                if len(out) >= limit:
                    return out
    return out


@mcp_tool(mcp)
async def search_repo(target_id: str, pattern: str, glob: str | None = None, max_results: int = 20,
                      ctx: Context | None = None) -> Any:
    """레포 전체에서 정규식 검색 (라인 단위). 정규식 실행 타임아웃 1초.

    입력: target_id, pattern (Python 정규식), glob (선택), max_results (≤50)
    출력: [{path, line, preview_masked}]
    오류: VALIDATION(잘못된 정규식), UPSTREAM_TIMEOUT(1초 초과). 부작용: 없음.
    """
    if not 1 <= max_results <= 50:
        raise AppError(ErrorCode.VALIDATION, "max_results must be 1..50")
    if len(pattern) > 300:
        raise AppError(ErrorCode.VALIDATION, "pattern too long")
    try:
        compiled = re.compile(pattern)
    except re.error as e:
        raise AppError(ErrorCode.VALIDATION, f"invalid regex: {e}") from e
    t = await get_target(target_id, "repo")
    try:
        return await asyncio.wait_for(asyncio.to_thread(_search_python, t.root, compiled, glob, max_results),
                                      timeout=SEARCH_TIMEOUT_S)
    except TimeoutError as e:
        raise AppError(ErrorCode.UPSTREAM_TIMEOUT, "regex search exceeded 1s") from e


async def load_agent_files(target_id: str) -> dict[str, Any]:
    t = await get_target(target_id, "agent")
    manifest_p = safe_path(t.root, "manifest.yaml")
    prompt_p = safe_path(t.root, "system_prompt.md")
    if not manifest_p.is_file():
        raise AppError(ErrorCode.NOT_FOUND, "agent manifest not found (run make demo-reset)")
    manifest_text = manifest_p.read_text(encoding="utf-8")
    prompt = prompt_p.read_text(encoding="utf-8") if prompt_p.is_file() else ""
    return {"target": t, "manifest": yaml.safe_load(manifest_text) or {}, "manifest_text": manifest_text,
            "system_prompt": prompt}


@mcp_tool(mcp)
async def get_agent_manifest(target_id: str, ctx: Context | None = None) -> Any:
    """에이전트 대상의 매니페스트(도구·권한·스코프)와 시스템 프롬프트(마스킹)를 반환한다.

    입력: target_id (type=agent)
    출력: {manifest, manifest_path, system_prompt_masked, system_prompt_path, linked_target_id}
    오류: NOT_FOUND, VALIDATION(repo 대상). 부작용: 없음.
    """
    a = await load_agent_files(target_id)
    return {"manifest": a["manifest"], "manifest_path": "manifest.yaml",
            "system_prompt_masked": mask_text(a["system_prompt"]), "system_prompt_path": "system_prompt.md",
            "linked_target_id": a["target"].linked_target_id}


def finding_dict(f: Finding) -> dict[str, Any]:
    return {"finding_id": str(f.id), "audit_id": str(f.audit_id), "target_id": str(f.target_id), "source": f.source,
            "category": f.category, "rule_id": f.rule_id, "title": f.title, "severity": f.severity,
            "confidence": f.confidence, "status": f.status, "fingerprint": f.fingerprint, "evidence": f.evidence,
            "reasoning_summary": f.reasoning_summary, "tools_used": f.tools_used, "standard_refs": f.standard_refs,
            "fix_hint": f.fix_hint, "created_at": f.created_at.isoformat() if f.created_at else None}


def _uuid(v: str, name: str) -> uuid.UUID:
    try:
        return uuid.UUID(v)
    except ValueError as e:
        raise AppError(ErrorCode.VALIDATION, f"{name} must be a UUID") from e


@mcp_tool(mcp)
async def get_findings(audit_id: str | None = None, target_id: str | None = None, status: str | None = None,
                       severity: str | None = None, ctx: Context | None = None) -> Any:
    """저장된 Finding 목록을 필터로 조회한다.

    입력: audit_id?, target_id?, status?, severity? (최소 1개 권장)
    출력: Finding 목록 (evidence, tools_used, reasoning_summary 포함)
    오류: VALIDATION(UUID 형식). 부작용: 없음.
    """
    q = select(Finding).order_by(Finding.created_at)
    if audit_id:
        q = q.where(Finding.audit_id == _uuid(audit_id, "audit_id"))
    if target_id:
        q = q.where(Finding.target_id == _uuid(target_id, "target_id"))
    if status:
        q = q.where(Finding.status == status)
    if severity:
        q = q.where(Finding.severity == severity)
    async with session_scope() as db:
        return [finding_dict(f) for f in (await db.execute(q.limit(500))).scalars()]


@mcp_tool(mcp)
async def get_audit_history(target_id: str, limit: int = 5, ctx: Context | None = None) -> Any:
    """대상의 과거 감사 요약 (최신순).

    입력: target_id, limit (기본 5, ≤20)
    출력: [{audit_id, status, mode, started_at, finished_at, summary}]
    오류: VALIDATION. 부작용: 없음.
    """
    tid = _uuid(target_id, "target_id")
    q = (select(Audit).join(AuditTarget, AuditTarget.audit_id == Audit.id).where(AuditTarget.target_id == tid)
         .order_by(Audit.created_at.desc()).limit(min(max(limit, 1), 20)))
    async with session_scope() as db:
        rows = (await db.execute(q)).scalars()
        return [{"audit_id": str(a.id), "status": a.status, "mode": a.mode,
                 "started_at": a.started_at.isoformat() if a.started_at else None,
                 "finished_at": a.finished_at.isoformat() if a.finished_at else None, "summary": a.summary}
                for a in rows]
