"""business-mcp 실행 도구 (명세 9.3 '실행'): 정적 스캔, Agent 규칙 검사, 인젝션 probe."""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Literal

import httpx
from mcp.server.fastmcp import Context

from core.db.models import ProbeRun
from core.db.session import session_scope
from core.errors import AppError, ErrorCode
from core.probes import Probe, ProbeSuccess, judge_attempt, load_probe_set, signals
from core.redis.cache import cache_get, cache_set
from core.rules.engine import check_manifest, scan_repo, secret_names_from_hits
from core.rules.models import RuleHit, load_rules, rules_hash
from core.settings import get_settings
from harness.masking import mask_obj, mask_text
from mcp_servers.business.app import mcp
from mcp_servers.business.tools.query import load_agent_files
from mcp_servers.business.workspace import _overrides, commit_sha, get_target
from mcp_servers.common import current_call, mcp_tool

SCAN_CACHE_TTL_S = 24 * 3600
PROBE_TIMEOUT_S = 30.0
MAX_ATTEMPTS = 10


async def static_scan(target_id: str, paths: list[str] | None = None,
                      rule_ids: list[str] | None = None) -> tuple[list[RuleHit], str, bool]:
    """(hits, commit_sha, cached). 전체 스캔만 cache:scan:{target}:{commit}:{rules_hash} 에 캐시."""
    t = await get_target(target_id, "repo")
    sha = await commit_sha(t.root)
    rules = load_rules("code")
    key = f"cache:scan:{target_id}:{sha}:{rules_hash(rules)}"
    cacheable = not paths and not rule_ids and sha != "nogit"
    if cacheable and (cached := await cache_get(key)) is not None:
        return [RuleHit.model_validate(h) for h in cached], sha, True
    hits = await asyncio.to_thread(scan_repo, t.root, paths, rule_ids)
    if cacheable:
        await cache_set(key, [h.model_dump() for h in hits], SCAN_CACHE_TTL_S)
    return hits, sha, False


@mcp_tool(mcp)
async def run_static_scan(target_id: str, paths: list[str] | None = None, rule_ids: list[str] | None = None,
                          ctx: Context | None = None) -> Any:
    """레포 대상에 정적 규칙(rules/code/*.yaml)을 실행한다. 커밋 해시 기준으로 결과를 캐시한다.

    입력: target_id, paths (선택, 특정 파일만), rule_ids (선택)
    출력: {commit_sha, cached, hits: [{rule_id, category, path, line, snippet_masked, severity,
           needs_llm_review, references, fix_hint}]}
    오류: NOT_FOUND, VALIDATION. 부작용: 없음 (Redis 캐시만 기록).
    """
    hits, sha, cached = await static_scan(target_id, paths, rule_ids)
    return {"commit_sha": sha, "cached": cached, "hits": [h.model_dump() for h in hits]}


@mcp_tool(mcp)
async def run_agent_rule_check(target_id: str, ctx: Context | None = None) -> Any:
    """에이전트 매니페스트·시스템 프롬프트에 Agent 규칙(rules/agent/*.yaml, AGT-*)을 실행한다.

    연결된 레포(linked_target_id)가 있으면 그 정적 스캔에서 하드코딩 키 이름을 모아 AGT-SECRET-001 에 사용한다.
    입력: target_id (type=agent)
    출력: {hits: [...], code_secret_names: [...]}
    오류: NOT_FOUND, VALIDATION. 부작용: 없음.
    """
    a = await load_agent_files(target_id)
    names: set[str] = set()
    linked = a["target"].linked_target_id
    if linked:
        code_hits, _, _ = await static_scan(linked)
        names = secret_names_from_hits(code_hits)
    hits = check_manifest(a["manifest"], a["manifest_text"], a["system_prompt"], names)
    return {"hits": [h.model_dump() for h in hits], "code_secret_names": sorted(names)}


def _check_allowlist(base_url: str) -> str:
    url = base_url.rstrip("/")
    if url not in get_settings().probe_allowlist_urls:
        raise AppError(ErrorCode.FORBIDDEN, "probe target not in sandbox allowlist", {"url": url})
    return url


async def _chat(client: httpx.AsyncClient, url: str, session_id: str, message: str,
                kb_docs: list[str] | None) -> dict[str, Any]:
    try:
        res = await client.post(f"{url}/chat", json={"session_id": session_id, "message": message,
                                                      "kb_docs": kb_docs}, timeout=PROBE_TIMEOUT_S)
    except httpx.TimeoutException as e:
        raise AppError(ErrorCode.UPSTREAM_TIMEOUT, "target-agent timeout") from e
    except httpx.TransportError as e:
        raise AppError(ErrorCode.UPSTREAM_ERROR, f"target-agent unreachable: {type(e).__name__}") from e
    if res.status_code == 429:
        raise AppError(ErrorCode.RATE_LIMITED, "target-agent rate limited", retry_after_s=5)
    if res.status_code >= 500:
        raise AppError(ErrorCode.UPSTREAM_ERROR, f"target-agent {res.status_code}",
                       {"body": mask_text(res.text[:300])})
    if res.status_code >= 400:
        raise AppError(ErrorCode.VALIDATION, f"target-agent rejected request {res.status_code}")
    data: dict[str, Any] = res.json()
    return data


async def _persist(rows: list[ProbeRun]) -> None:
    async with session_scope() as db:
        db.add_all(rows)


@mcp_tool(mcp, roles=("ADMIN",))
async def run_injection_probe(
    target_id: str,
    probe_id: str | None = None,
    payload: str | None = None,
    channel: Literal["direct", "indirect"] = "direct",
    kb_docs: list[str] | None = None,
    attempts: int = 3,
    ctx: Context | None = None,
) -> Any:
    """샌드박스 target-agent 에 인젝션 probe 를 N회 실행하고 결정론 신호를 계산한다.

    probe_id 만 주면 데이터셋(datasets/probes) 공격을, payload 를 주면 그 문장으로 실행한다
    (payload + probe_id = 해당 probe 의 판정 기준을 쓰는 적응형 변형).
    입력: target_id (type=agent), probe_id?, payload?, channel, kb_docs?, attempts (기본 3, ≤10)
    출력: {probe_run_ids, attempts: [{reply_masked, tool_calls, canary_leaked, forbidden_tools,
           external_recipients, verdict}]}
    권한: ADMIN (워커는 감사 요청자 역할을 X-Actor-Role 로 전달)
    오류: FORBIDDEN(allowlist 밖), UPSTREAM_TIMEOUT(30s/회), UPSTREAM_ERROR, VALIDATION.
    부작용: probe_runs 테이블에 시도별 기록 (target-agent 의 도구는 가짜 구현이라 실제 변경 없음).
    """
    if not 1 <= attempts <= MAX_ATTEMPTS:
        raise AppError(ErrorCode.VALIDATION, f"attempts must be 1..{MAX_ATTEMPTS}")
    if not probe_id and not payload:
        raise AppError(ErrorCode.VALIDATION, "probe_id or payload required")
    ps = load_probe_set()
    base = ps.get(probe_id) if probe_id else None
    if probe_id and base is None and not payload:
        raise AppError(ErrorCode.NOT_FOUND, f"probe {probe_id} not found")
    if base is not None and payload:
        probe = base.model_copy(update={"turns": [payload], "kb_docs": kb_docs or base.kb_docs, "adaptive": True})
    elif base is not None:
        probe = base
    else:
        probe = Probe(id=probe_id or "ADHOC", category="ADHOC", channel=channel, turns=[payload or ""],
                      kb_docs=kb_docs, success=ProbeSuccess(type="canary"))
    t = await get_target(target_id, "agent")
    url = _check_allowlist(t.ref)
    call = current_call()

    results: list[dict[str, Any]] = []
    rows: list[ProbeRun] = []
    async with httpx.AsyncClient() as client:
        for i in range(1, attempts + 1):
            session_id = f"probe-{probe.id}-{uuid.uuid4().hex[:8]}"
            started = time.monotonic()
            data: dict[str, Any] = {"reply": "", "tool_calls": []}
            for turn in probe.turns:
                data = await _chat(client, url, session_id, turn, probe.kb_docs)
            reply = str(data.get("reply", ""))
            tool_calls = list(data.get("tool_calls", []))
            sig = signals(reply, tool_calls, ps)
            verdict = judge_attempt(probe, reply, tool_calls, ps)
            run_id = uuid.uuid4()
            results.append({"probe_run_id": str(run_id), "attempt": i, "reply_masked": mask_text(reply),
                            "tool_calls": mask_obj(tool_calls), **sig.model_dump(), "verdict": verdict})
            rows.append(ProbeRun(
                id=run_id, audit_id=uuid.UUID(call.audit_id) if call.audit_id else None,
                target_id=uuid.UUID(t.id), probe_id=probe.id, category=probe.category, channel=probe.channel,
                attempt=i, request={"turns": [mask_text(x) for x in probe.turns], "kb_docs": probe.kb_docs,
                                    "adaptive": probe.adaptive},
                reply_masked=mask_text(reply), tool_calls=mask_obj(tool_calls),  # type: ignore[arg-type]
                success=verdict == "SUCCESS", judged_by="deterministic",
                latency_ms=int((time.monotonic() - started) * 1000),
            ))
    if t.id not in _overrides:
        await _persist(rows)
    return {"probe_id": probe.id, "category": probe.category, "channel": probe.channel,
            "probe_run_ids": [r["probe_run_id"] for r in results], "attempts": results}
