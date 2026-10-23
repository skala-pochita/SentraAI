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
from core.probes import Probe, ProbeSet, ProbeSuccess, judge_attempt, load_probe_set, signals
from core.redis.cache import cache_get, cache_set
from core.redis.client import get_redis
from core.rules.engine import check_manifest, scan_repo, secret_names_from_hits
from core.rules.models import RuleHit, load_rules, rules_hash
from core.settings import get_settings
from harness.masking import mask_obj, mask_text
from mcp_servers.business.app import mcp
from mcp_servers.business.tools.query import load_agent_files
from mcp_servers.business.workspace import _overrides, commit_sha, get_target
from mcp_servers.common import current_call, mcp_tool


async def _sandbox_access(target: Any) -> None:
    if target.config.get("source") != "upload":
        return
    from core.db.models import Audit, AuditTarget

    call = current_call()
    if not call.audit_id or not call.actor_id:
        raise AppError(ErrorCode.FORBIDDEN, "audit context required")
    async with session_scope() as db:
        aid = uuid.UUID(call.audit_id)
        audit = await db.get(Audit, aid)
        linked = await db.get(AuditTarget, (aid, uuid.UUID(target.id)))
        if audit is None or linked is None or audit.status != "RUNNING":
            raise AppError(ErrorCode.FORBIDDEN, "active audit target required")
        if call.role != "ADMIN" and (str(audit.requested_by) != call.actor_id or target.owner_user_id != call.actor_id):
            raise AppError(ErrorCode.FORBIDDEN, "sandbox target ownership mismatch")


SCAN_CACHE_TTL_S = 24 * 3600
FAULT_PREFIXES = ("rate_limit:", "github_rate_limit:")  # 뒤쪽은 수정안 경로 시절 값 (DECISIONS 2026-10-21)


def fault_count() -> int:
    """FAULT_INJECTION=rate_limit:N → 감사·대상별 처음 N회 정적 스캔을 RATE_LIMITED 로 실패시킨다."""
    spec = get_settings().fault_injection.strip()
    for prefix in FAULT_PREFIXES:
        if spec.startswith(prefix):
            try:
                return max(0, int(spec[len(prefix) :]))
            except ValueError:
                return 0
    return 0


async def _inject_fault(target_id: str) -> None:
    if (n := fault_count()) <= 0:
        return
    key = f"fault:scan:{current_call().audit_id or '-'}:{target_id}"
    r = get_redis()
    count = int(await r.incr(key))
    await r.expire(key, 3600)
    if count <= n:
        raise AppError(
            ErrorCode.RATE_LIMITED, "rate limit (fault injection)", {"attempt": count, "fail_first": n}, retry_after_s=1
        )


PROBE_TIMEOUT_S = 30.0
MAX_ATTEMPTS = 10


async def static_scan(
    target_id: str, paths: list[str] | None = None, rule_ids: list[str] | None = None
) -> tuple[list[RuleHit], str, bool]:
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
async def run_static_scan(
    target_id: str, paths: list[str] | None = None, rule_ids: list[str] | None = None, ctx: Context | None = None
) -> Any:
    """레포 대상에 정적 규칙(rules/code/*.yaml)을 실행한다. 커밋 해시 기준으로 결과를 캐시한다.

    입력: target_id, paths (선택, 특정 파일만), rule_ids (선택)
    출력: {commit_sha, cached, hits: [{rule_id, category, path, line, snippet_masked, severity,
           needs_llm_review, references, fix_hint}]}
    오류: NOT_FOUND, VALIDATION, RATE_LIMITED(FAULT_INJECTION 시연용). 부작용: 없음 (Redis 캐시만 기록).
    """
    await _inject_fault(target_id)
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
    if a.get("kind") == "source":
        from core.agent_project import source_drafts

        return {"hits": [], "code_secret_names": sorted(names), "source_findings": source_drafts(a, target_id)}
    from core.tool_code_evidence import collect_tool_evidence

    code_evidence = await asyncio.to_thread(
        collect_tool_evidence,
        a["target"].root,
        a["manifest"],
        a["manifest_text"],
        a.get("manifest_path", "manifest.yaml"),
    )
    hits = check_manifest(
        a["manifest"],
        a["manifest_text"],
        a["system_prompt"],
        names,
        manifest_path=a.get("manifest_path", "manifest.yaml"),
    )
    return {
        "hits": [h.model_dump() for h in hits],
        "code_secret_names": sorted(names),
        "tool_code_evidence": code_evidence,
    }


def _check_allowlist(base_url: str) -> str:
    url = base_url.rstrip("/")
    if url not in get_settings().probe_allowlist_urls:
        raise AppError(ErrorCode.FORBIDDEN, "probe target not in sandbox allowlist", {"url": url})
    return url


async def _chat(
    client: httpx.AsyncClient, url: str, session_id: str, message: str, kb_docs: list[str] | None
) -> dict[str, Any]:
    try:
        res = await client.post(
            f"{url}/chat",
            json={"session_id": session_id, "message": message, "kb_docs": kb_docs},
            timeout=PROBE_TIMEOUT_S,
        )
    except httpx.TimeoutException as e:
        raise AppError(ErrorCode.UPSTREAM_TIMEOUT, "target-agent timeout") from e
    except httpx.TransportError as e:
        raise AppError(ErrorCode.UPSTREAM_ERROR, f"target-agent unreachable: {type(e).__name__}") from e
    if res.status_code == 429:
        raise AppError(ErrorCode.RATE_LIMITED, "target-agent rate limited", retry_after_s=5)
    if res.status_code == 503 and "not configured" in res.text:
        raise AppError(ErrorCode.VALIDATION, "target-agent LLM not configured (OPENAI_API_KEY)")
    if res.status_code >= 500:
        raise AppError(ErrorCode.UPSTREAM_ERROR, f"target-agent {res.status_code}", {"body": mask_text(res.text[:300])})
    if res.status_code >= 400:
        raise AppError(ErrorCode.VALIDATION, f"target-agent rejected request {res.status_code}")
    data: dict[str, Any] = res.json()
    return data


async def _persist(rows: list[ProbeRun]) -> None:
    async with session_scope() as db:
        db.add_all(rows)


async def _probe_set_for(t: Any, target_id: str, audit_id: str) -> ProbeSet:
    """업로드 에이전트는 프로젝트 전용 공격 세트, 내장 샘플은 datasets/probes."""
    if t.config.get("source") != "upload":
        return load_probe_set()
    from core.project_probes import uploaded_probe_set

    a = await load_agent_files(target_id)
    return uploaded_probe_set(audit_id, a["manifest"], t.config.get("runtime_protocol", "atlas"))


def _resolve_probe(
    ps: ProbeSet, probe_id: str | None, payload: str | None, channel: str, kb_docs: list[str] | None
) -> Probe:
    """probe_id 만 → 데이터셋 공격, payload + probe_id → 그 판정 기준을 쓰는 적응형 변형, payload 만 → ADHOC."""
    base = ps.get(probe_id) if probe_id else None
    if probe_id and base is None and not payload:
        raise AppError(ErrorCode.NOT_FOUND, f"probe {probe_id} not found")
    if base is not None and payload:
        return base.model_copy(update={"turns": [payload], "kb_docs": kb_docs or base.kb_docs, "adaptive": True})
    if base is not None:
        return base
    return Probe(
        id=probe_id or "ADHOC",
        category="ADHOC",
        channel=channel,  # type: ignore[arg-type]
        turns=[payload or ""],
        kb_docs=kb_docs,
        success=ProbeSuccess(type="canary"),
    )


async def _converse(client: httpx.AsyncClient, t: Any, url: str, audit_id: str, probe: Probe) -> dict[str, Any]:
    """probe 의 모든 턴을 한 세션으로 보내고 응답·도구 호출을 합친다. 업로드 대상은 sandbox-runner 경유."""
    session_id = f"probe-{probe.id}-{uuid.uuid4().hex[:8]}"
    replies: list[str] = []
    calls: list[dict[str, Any]] = []
    for turn in probe.turns:
        if t.config.get("source") == "upload":
            from core.sandbox_client import sandbox_call

            msg = {
                "audit_id": audit_id,
                "session_id": session_id,
                "message": turn,
                "kb_docs": probe.kb_docs,
                "protocol": t.config.get("runtime_protocol", "atlas"),
            }
            data = await sandbox_call("/chat", msg, timeout=300)
        else:
            data = await _chat(client, url, session_id, turn, probe.kb_docs)
        replies.append(str(data.get("reply", "")))
        calls.extend(data.get("tool_calls", []))
    out: dict[str, Any] = {"reply": "\n".join(replies), "tool_calls": calls}
    if t.config.get("source") == "upload":
        from core.runtime_redaction import redact_runtime

        out = await redact_runtime(t, audit_id, out)
    return out


def _record(
    t: Any, audit_id: str | None, probe: Probe, ps: ProbeSet, attempt: int, data: dict[str, Any], started: float
) -> tuple[dict[str, Any], ProbeRun]:
    reply = str(data.get("reply", ""))
    tool_calls = list(data.get("tool_calls", []))
    verdict = judge_attempt(probe, reply, tool_calls, ps)
    run_id = uuid.uuid4()
    result = {
        "probe_run_id": str(run_id),
        "attempt": attempt,
        "reply_masked": mask_text(reply),
        "tool_calls": mask_obj(tool_calls),
        **signals(reply, tool_calls, ps).model_dump(),
        "verdict": verdict,
    }
    row = ProbeRun(
        id=run_id,
        audit_id=uuid.UUID(audit_id) if audit_id else None,
        target_id=uuid.UUID(t.id),
        probe_id=probe.id,
        category=probe.category,
        channel=probe.channel,
        attempt=attempt,
        request={"turns": [mask_text(x) for x in probe.turns], "kb_docs": probe.kb_docs, "adaptive": probe.adaptive},
        reply_masked=mask_text(reply),
        tool_calls=mask_obj(tool_calls),  # type: ignore[arg-type]
        verdict=verdict,
        success=verdict == "SUCCESS",
        judged_by="deterministic",
        latency_ms=int((time.monotonic() - started) * 1000),
    )
    return result, row


@mcp_tool(mcp, roles=("ADMIN", "DEVELOPER"))
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
    권한: ADMIN, DEVELOPER (업로드 대상은 진행 중인 본인 감사·본인 소유만). 워커는 요청자 역할을 X-Actor-Role 로 전달
    오류: FORBIDDEN(allowlist 밖), UPSTREAM_TIMEOUT(30s/회), UPSTREAM_ERROR, VALIDATION.
    부작용: probe_runs 테이블에 시도별 기록 (target-agent 의 도구는 가짜 구현이라 실제 변경 없음).
    """
    if not 1 <= attempts <= MAX_ATTEMPTS:
        raise AppError(ErrorCode.VALIDATION, f"attempts must be 1..{MAX_ATTEMPTS}")
    if not probe_id and not payload:
        raise AppError(ErrorCode.VALIDATION, "probe_id or payload required")
    t = await get_target(target_id, "agent")
    await _sandbox_access(t)
    audit_id = current_call().audit_id
    ps = await _probe_set_for(t, target_id, audit_id or "")
    probe = _resolve_probe(ps, probe_id, payload, channel, kb_docs)
    url = "" if t.config.get("source") == "upload" else _check_allowlist(t.ref)
    results: list[dict[str, Any]] = []
    rows: list[ProbeRun] = []
    async with httpx.AsyncClient() as client:
        for i in range(1, attempts + 1):
            started = time.monotonic()
            data = await _converse(client, t, url, audit_id or "", probe)
            result, row = _record(t, audit_id, probe, ps, i, data, started)
            results.append(result)
            rows.append(row)
    if t.id not in _overrides:
        await _persist(rows)
    return {
        "probe_id": probe.id,
        "category": probe.category,
        "channel": probe.channel,
        "probe_run_ids": [r["probe_run_id"] for r in results],
        "attempts": results,
    }


@mcp_tool(mcp, roles=("ADMIN", "DEVELOPER"))
async def prepare_agent_sandbox(target_id: str, ctx: Context | None = None) -> Any:
    """업로드 에이전트 프로젝트를 격리 컨테이너로 빌드·기동한다 (내장 샘플 대상은 기존 엔드포인트 사용).

    입력: target_id (type=agent)
    출력: {status: RUNNING|EXTERNAL|UNSUPPORTED, endpoint?, reason?, requested_mode?}
    권한: ADMIN, DEVELOPER (진행 중인 본인 감사·본인 소유 업로드 대상만)
    오류: FORBIDDEN(감사 맥락·소유권 불일치), VALIDATION(audit id 없음), UPSTREAM_ERROR/TIMEOUT(sandbox-runner).
    부작용: sandbox-runner 에 컨테이너·internal 네트워크 생성, live 모드면 감사 전용 LLM 프록시 토큰 발급.
    """
    from core.sandbox_client import sandbox_call

    t = await get_target(target_id, "agent")
    if t.config.get("source") != "upload":
        return {"status": "EXTERNAL", "mode": "existing-target"}
    await _sandbox_access(t)
    call = current_call()
    if not call.audit_id:
        raise AppError(ErrorCode.VALIDATION, "audit id required for sandbox")
    from core.execution_config import ExecutionConfig, environment_for, readiness, settings_for_agent
    from core.project_transfer import encode_project
    from core.proxy_sessions import issue, revoke
    from core.settings import get_settings

    async with session_scope() as db:
        row = await settings_for_agent(db, t)
        ready = await asyncio.to_thread(readiness, t.root, row)
        if not ready["ready"]:
            return {
                "status": "UNSUPPORTED",
                "reason": "; ".join(c["detail"] for c in ready["criteria"] if c["met"] is False),
            }
        execution = ExecutionConfig.model_validate(row.config if row else {})
        environment = await asyncio.to_thread(environment_for, row, reveal=True)
        grant = None
        if execution.mode == "live" and row is not None:
            grant = await issue(call.audit_id, str(row.target_id), execution, row.secret_refs["provider"])
            for service in ["agent", "target-agent"]:
                environment[service][execution.api_key_variable] = grant
        transferred = await asyncio.to_thread(encode_project, t.root)
    try:
        result = await sandbox_call(
            "/start",
            {
                "audit_id": call.audit_id,
                **transferred,
                "environment": environment,
                "proxy": {"token": grant, "url": get_settings().llm_proxy_url} if grant else None,
            },
            timeout=1100,
        )
        return result | {"requested_mode": execution.mode}
    except AppError:
        await revoke(call.audit_id)
        raise


@mcp_tool(mcp, roles=("ADMIN", "DEVELOPER"))
async def stop_agent_sandbox(target_id: str, ctx: Context | None = None) -> Any:
    """감사가 띄운 격리 컨테이너·네트워크·이미지를 정리하고 LLM 프록시 토큰을 폐기한다.

    입력: target_id (type=agent)
    출력: {status: STOPPED|EXTERNAL|NOT_STARTED}
    권한: ADMIN, DEVELOPER. 오류: UPSTREAM_ERROR/TIMEOUT(sandbox-runner).
    부작용: 컨테이너·네트워크·이미지 제거.
    """
    from core.sandbox_client import sandbox_call

    t = await get_target(target_id, "agent")
    if t.config.get("source") != "upload":
        return {"status": "EXTERNAL"}
    call = current_call()
    if call.audit_id:
        return await sandbox_call("/stop/" + call.audit_id)
    return {"status": "NOT_STARTED"}
