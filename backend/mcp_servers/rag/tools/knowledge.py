"""rag-mcp 도구 (명세 9.4)."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from mcp.server.fastmcp import Context
from sqlalchemy import or_, select

from core.db.models import Finding, FpRule, KbChunk, KbDocument
from core.db.session import session_scope
from core.errors import AppError, ErrorCode
from mcp_servers.common import current_call, mcp_tool
from mcp_servers.rag.app import mcp
from mcp_servers.rag.ingest import bump_rag_version, upsert_document
from mcp_servers.rag.search import STANDARD_SOURCES, hybrid_search

StandardSource = Literal["owasp_top10", "owasp_llm", "owasp_agentic", "cwe"]


def _k(k: int) -> int:
    if not 1 <= k <= 20:
        raise AppError(ErrorCode.VALIDATION, "k must be 1..20")
    return k


def _uuid(v: str, name: str) -> uuid.UUID:
    try:
        return uuid.UUID(v)
    except ValueError as e:
        raise AppError(ErrorCode.VALIDATION, f"{name} must be a UUID") from e


@mcp_tool(mcp)
async def search_security_standard(
    query: str, source: StandardSource | None = None, k: int = 5, ctx: Context | None = None
) -> Any:
    """보안 표준(OWASP Top10 2021, OWASP LLM Top10, OWASP Agentic, CWE)을 하이브리드 검색한다.

    입력: query (자연어·키워드), source (선택: owasp_top10|owasp_llm|owasp_agentic|cwe), k (기본 5)
    출력: [{chunk_id, doc_title, section, text, score, ref_code}]
    오류: VALIDATION(k 범위). 부작용: 없음 (결과 1시간 캐시).
    """
    return await hybrid_search(query, (source,) if source else STANDARD_SOURCES, _k(k))


@mcp_tool(mcp)
async def search_framework_guide(
    query: str, framework: str = "spring_security", k: int = 5, ctx: Context | None = None
) -> Any:
    """프레임워크 보안 설정 가이드(현재 spring_security)를 검색한다. 올바른 설정 예시 코드 포함.

    입력: query, framework (기본 "spring_security"), k
    출력: [{chunk_id, doc_title, section, text, score, ref_code}]
    오류: VALIDATION. 부작용: 없음.
    """
    if framework != "spring_security":
        raise AppError(ErrorCode.VALIDATION, "only framework=spring_security is indexed")
    return await hybrid_search(query, ("framework",), _k(k))


@mcp_tool(mcp)
async def search_company_policy(query: str, k: int = 5, ctx: Context | None = None) -> Any:
    """사내 보안 정책 문서를 검색한다 (비밀값 관리, 에이전트 도구 승인, 오탐 처리 등).

    입력: query, k
    출력: [{chunk_id, doc_title, section, text, score, ref_code}]
    오류: VALIDATION. 부작용: 없음.
    """
    return await hybrid_search(query, ("policy",), _k(k))


@mcp_tool(mcp)
async def search_past_cases(query: str, target_id: str | None = None, k: int = 5, ctx: Context | None = None) -> Any:
    """과거 Finding·조치 사례를 검색한다 (해결·오탐·위험수용 처리된 건 우선).

    입력: query (키워드), target_id (선택), k
    출력: [{finding_id, title, category, severity, status, reasoning_summary, fix_hint}] + 지식베이스 past_case 문서
    오류: VALIDATION. 부작용: 없음.
    """
    words = [w for w in query.split() if len(w) >= 2][:5] or [query]
    q = select(Finding).where(
        or_(
            *[
                Finding.title.ilike(f"%{w}%")
                | Finding.category.ilike(f"%{w}%")
                | Finding.reasoning_summary.ilike(f"%{w}%")
                for w in words
            ]
        )
    )
    if target_id:
        q = q.where(Finding.target_id == _uuid(target_id, "target_id"))
    q = (
        q.where(Finding.status.in_(["RESOLVED", "FALSE_POSITIVE", "ACCEPTED_RISK"]))
        .order_by(Finding.created_at.desc())
        .limit(_k(k))
    )
    async with session_scope() as db:
        cases = [
            {
                "finding_id": str(f.id),
                "title": f.title,
                "category": f.category,
                "severity": f.severity,
                "status": f.status,
                "reasoning_summary": f.reasoning_summary,
                "fix_hint": f.fix_hint,
            }
            for f in (await db.execute(q)).scalars()
        ]
    docs = await hybrid_search(query, ("past_case",), k)
    return {"cases": cases, "documents": docs}


@mcp_tool(mcp)
async def get_document(doc_id: str, chunk_id: str | None = None, ctx: Context | None = None) -> Any:
    """지식 문서 전체 또는 특정 청크 원문을 반환한다.

    입력: doc_id, chunk_id (선택)
    출력: {doc_id, title, source_type, ref_code, content_md} 또는 {chunk_id, section, text}
    오류: NOT_FOUND, VALIDATION. 부작용: 없음.
    """
    async with session_scope() as db:
        doc = await db.get(KbDocument, _uuid(doc_id, "doc_id"))
        if doc is None:
            raise AppError(ErrorCode.NOT_FOUND, "document not found")
        if chunk_id:
            ch = await db.get(KbChunk, _uuid(chunk_id, "chunk_id"))
            if ch is None or ch.doc_id != doc.id:
                raise AppError(ErrorCode.NOT_FOUND, "chunk not found")
            return {"chunk_id": str(ch.id), "doc_id": str(doc.id), "section": ch.section, "text": ch.text}
        return {
            "doc_id": str(doc.id),
            "title": doc.title,
            "source_type": doc.source_type,
            "ref_code": doc.ref_code,
            "content_md": doc.content_md,
        }


@mcp_tool(mcp)
async def list_documents(source_type: str | None = None, ctx: Context | None = None) -> Any:
    """지식 문서 목록 (본문 제외).

    입력: source_type (선택)
    출력: [{doc_id, title, source_type, ref_code, updated_at, chunks}]
    오류: 없음. 부작용: 없음.
    """
    async with session_scope() as db:
        q = select(KbDocument).order_by(KbDocument.title)
        if source_type:
            q = q.where(KbDocument.source_type == source_type)
        docs = list((await db.execute(q)).scalars())
        out = []
        for d in docs:
            n = len((await db.execute(select(KbChunk.id).where(KbChunk.doc_id == d.id))).all())
            out.append(
                {
                    "doc_id": str(d.id),
                    "title": d.title,
                    "source_type": d.source_type,
                    "ref_code": d.ref_code,
                    "updated_at": d.updated_at.isoformat(),
                    "chunks": n,
                }
            )
        return out


@mcp_tool(mcp)
async def list_false_positive_rules(
    target_id: str | None = None, rule_id: str | None = None, ctx: Context | None = None
) -> Any:
    """등록된 오탐 억제 규칙 목록. 조사 노드는 같은 fingerprint/rule 조합을 오탐으로 처리한다.

    입력: target_id (선택), rule_id (선택)
    출력: [{fp_rule_id, target_id, rule_id, fingerprint, reason, created_at}]
    오류: VALIDATION. 부작용: 없음.
    """
    q = select(FpRule).order_by(FpRule.created_at)
    if target_id:
        q = q.where(or_(FpRule.target_id == _uuid(target_id, "target_id"), FpRule.target_id.is_(None)))
    if rule_id:
        q = q.where(FpRule.rule_id == rule_id)
    async with session_scope() as db:
        return [
            {
                "fp_rule_id": str(r.id),
                "target_id": str(r.target_id) if r.target_id else None,
                "rule_id": r.rule_id,
                "fingerprint": r.fingerprint,
                "reason": r.reason,
                "created_at": r.created_at.isoformat(),
            }
            for r in (await db.execute(q)).scalars()
        ]


@mcp_tool(mcp, roles=("ADMIN",))
async def ingest_document(
    title: str, source_type: str, content_md: str, ref_code: str | None = None, ctx: Context | None = None
) -> Any:
    """지식 문서를 등록한다 (같은 제목이면 갱신). 마크다운 헤더 기준 청크·임베딩.

    입력: title, source_type (owasp_top10|owasp_llm|owasp_agentic|cwe|framework|policy|past_case), content_md, ref_code?
    출력: {doc_id, chunks}
    권한: ADMIN. 오류: VALIDATION, UPSTREAM_*(임베딩). 부작용: kb_documents/kb_chunks 쓰기, 검색 캐시 무효화.
    """
    async with session_scope() as db:
        doc, n = await upsert_document(db, title, source_type, content_md, ref_code)
        doc_id = str(doc.id)
    await bump_rag_version()
    return {"doc_id": doc_id, "chunks": n}


@mcp_tool(mcp, roles=("ADMIN",))
async def update_document(doc_id: str, content_md: str, ctx: Context | None = None) -> Any:
    """지식 문서 본문을 수정하고 재청크·재임베딩한다.

    입력: doc_id, content_md
    출력: {doc_id, chunks}
    권한: ADMIN. 오류: NOT_FOUND, VALIDATION. 부작용: 청크 교체, 검색 캐시 무효화.
    """
    async with session_scope() as db:
        doc = await db.get(KbDocument, _uuid(doc_id, "doc_id"))
        if doc is None:
            raise AppError(ErrorCode.NOT_FOUND, "document not found")
        _, n = await upsert_document(db, doc.title, doc.source_type, content_md, doc.ref_code, doc.id)
    await bump_rag_version()
    return {"doc_id": doc_id, "chunks": n}


@mcp_tool(mcp, roles=("ADMIN",))
async def delete_document(doc_id: str, ctx: Context | None = None) -> Any:
    """지식 문서와 청크를 삭제한다.

    입력: doc_id
    출력: {deleted: true}
    권한: ADMIN. 오류: NOT_FOUND. 부작용: 삭제(복구 불가), 검색 캐시 무효화.
    """
    async with session_scope() as db:
        doc = await db.get(KbDocument, _uuid(doc_id, "doc_id"))
        if doc is None:
            raise AppError(ErrorCode.NOT_FOUND, "document not found")
        await db.delete(doc)
    await bump_rag_version()
    return {"deleted": True}


@mcp_tool(mcp, roles=("ADMIN", "DEVELOPER"))
async def register_false_positive(
    finding_id: str, reason: str, rule_id: str | None = None, fingerprint: str | None = None, ctx: Context | None = None
) -> Any:
    """Finding 을 오탐으로 등록하고 억제 규칙(fp_rules)을 만든다. 다음 감사부터 같은 지문은 억제된다.

    입력: finding_id, reason (필수), rule_id?, fingerprint? (생략 시 Finding 값 사용)
    출력: {fp_rule_id, finding_status}
    권한: ADMIN, DEVELOPER(자기 대상만 — API 계층에서 검사 후 호출)
    오류: NOT_FOUND, VALIDATION, CONFLICT(이미 해결된 Finding). 부작용: fp_rules 추가, Finding 상태 FALSE_POSITIVE.
    """
    if not reason.strip():
        raise AppError(ErrorCode.VALIDATION, "reason required")
    call = current_call()
    if not call.actor_id:
        raise AppError(ErrorCode.VALIDATION, "X-Actor-Id header required")
    async with session_scope() as db:
        f = await db.get(Finding, _uuid(finding_id, "finding_id"))
        if f is None:
            raise AppError(ErrorCode.NOT_FOUND, "finding not found")
        if f.status not in ("OPEN", "FALSE_POSITIVE"):
            raise AppError(ErrorCode.CONFLICT, f"cannot mark {f.status} finding as false positive")
        rule = FpRule(
            target_id=f.target_id,
            rule_id=rule_id or f.rule_id,
            fingerprint=fingerprint or f.fingerprint,
            reason=reason,
            created_by=_uuid(call.actor_id, "actor_id"),
        )
        db.add(rule)
        f.status = "FALSE_POSITIVE"
        await db.flush()
        return {"fp_rule_id": str(rule.id), "finding_status": f.status}
