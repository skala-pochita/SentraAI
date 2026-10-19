"""Agent·Cloud 감사 실행 (기획서 6장). 두 구성이 같은 ToolHost·예산·마스킹·출력 스키마를 쓴다.

B (harness): load_evidence → plan_audit → collect → assess → build_paths → verify ─(오류)→ repair_collect →
             repair_assess_paths → verify → draft_report → prepare_action → approval(interrupt) → execute_action → finalize
A (single):  같은 도구로 조회하는 단순 루프. plan·verify·repair·노드 분해 없음.

LLM 이 없으면 B 는 `llm_used=false` 로 결정론 점검(core.cloud.checks)만 수행한다. 이 결과는 A/B 평가에 쓰지 않는다.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field

from core.cloud.budget import CloudBudget
from core.cloud.bundle import EvidenceBundle
from core.cloud.checks import CloudFinding, _reachable, audit_bundle
from core.cloud.llm import StageRunner
from core.cloud.tickets import args_hash
from core.cloud.tools import ToolHost
from core.errors import AppError, ErrorCode
from harness.context import wrap_untrusted

# ---------------- LLM 출력 스키마 ----------------


class PlanOut(BaseModel):
    checks: list[str] = Field(default_factory=list)
    guidance_queries: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class Call(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class CollectOut(BaseModel):
    calls: list[Call] = Field(default_factory=list)
    done: bool = True


class AssessOut(BaseModel):
    findings: list[CloudFinding] = Field(default_factory=list)
    normal: list[str] = Field(default_factory=list)
    not_checked: list[str] = Field(default_factory=list)


class PathOut(BaseModel):
    findings: list[CloudFinding] = Field(default_factory=list)
    normal: list[str] = Field(default_factory=list)


class ProposedAction(BaseModel):
    title: str
    target: str
    body: str = ""


class ReportOut(BaseModel):
    summary: str = ""
    proposed_action: ProposedAction | None = None


class SingleFinal(BaseModel):
    findings: list[CloudFinding] = Field(default_factory=list)
    normal: list[str] = Field(default_factory=list)
    not_checked: list[str] = Field(default_factory=list)
    summary: str = ""


class SingleOut(BaseModel):
    calls: list[Call] = Field(default_factory=list)
    final: SingleFinal | None = None


# ---------------- 컨텍스트 ----------------


@dataclass
class CloudCtx:
    task_id: str
    bundle: EvidenceBundle
    host: ToolHost
    budget: CloudBudget
    runner: StageRunner | None  # None = LLM 미사용
    allowed_uris: list[str]
    events: list[dict[str, Any]] = field(default_factory=list)
    enable_action: bool = False
    partial: dict[str, Any] = field(default_factory=dict)  # 예산 소진 시 부분 결과 보존
    seq: int = 0
    flushed: int = 0
    on_events: Callable[[list[dict[str, Any]]], Awaitable[None]] | None = None  # 새 이벤트를 즉시 저장·중계
    on_report: Callable[[dict[str, Any]], Awaitable[None]] | None = None  # DONE 전에 보고서를 저장 (저장 성공 후 DONE)

    def emit(self, type_: str, data: dict[str, Any]) -> None:
        self.seq += 1
        self.events.append({"eventSeq": self.seq, "type": type_, "data": data})

    async def flush(self) -> None:
        """아직 전달하지 않은 이벤트를 on_events 로 보낸다 (노드가 끝날 때마다 호출)."""
        if self.on_events is not None and self.flushed < len(self.events):
            batch = self.events[self.flushed :]
            self.flushed = len(self.events)
            await self.on_events(batch)


class CState(TypedDict, total=False):
    evidence: dict[str, Any]
    plan: dict[str, Any]
    guidance: list[dict[str, Any]]
    findings: list[dict[str, Any]]
    normal: list[str]
    not_checked: list[str]
    verification_errors: list[str]
    repaired: bool
    summary: str
    proposed_action: dict[str, Any] | None
    action: dict[str, Any] | None
    budget_snapshot: dict[str, Any]
    approval: dict[str, Any]
    action_result: dict[str, Any]
    status: str
    failure: dict[str, Any] | None


def _ctx(config: RunnableConfig) -> CloudCtx:
    c = (config.get("configurable") or {}).get("ctx")
    if not isinstance(c, CloudCtx):
        raise AppError(ErrorCode.INTERNAL, "cloud ctx not configured")
    return c


def _dump(items: list[CloudFinding]) -> list[dict[str, Any]]:
    return [f.model_dump() for f in items]


def _load(items: list[dict[str, Any]]) -> list[CloudFinding]:
    return [CloudFinding.model_validate(x) for x in items]


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1)


def _untrusted_evidence(ctx: CloudCtx, evidence: dict[str, Any]) -> str:
    return wrap_untrusted(_json(ctx.host.masker.payload(evidence)), "evidence_bundle")


# ---------------- 노드 ----------------


async def load_evidence(state: CState, config: RunnableConfig) -> dict[str, Any]:
    ctx = _ctx(config)
    ctx.budget.resume_clock()
    res = await ctx.host.call("business_get_audit_evidence", {})
    if not res.ok and res.error_code == str(ErrorCode.BUDGET_EXCEEDED):
        code = "TIME_BUDGET_EXCEEDED" if ctx.budget.remaining_s <= 0 else "TOOL_CALL_LIMIT"
        raise AppError(ErrorCode.BUDGET_EXCEEDED, "budget exhausted before evidence load", {"code": code})
    if not res.ok:
        raise AppError(ErrorCode.UPSTREAM_ERROR, "evidence load failed", {"tool_result": res.content[:300]})
    data = json.loads(res.content.removesuffix("…[truncated]")) if not res.truncated else _fallback_evidence(ctx)
    return {"evidence": data}


def _fallback_evidence(ctx: CloudCtx) -> dict[str, Any]:
    """도구 결과가 8000자를 넘어 잘린 경우 종류별로 나눠 다시 조회하는 대신 마스킹된 번들 요약을 사용한다."""
    b = ctx.bundle
    return {
        "bundle_id": b.bundle_id,
        "items": [],
        "truncated": True,
        "business_policy": b.business_policy.model_dump(),
        "missing_evidence": [m.model_dump() for m in b.missing_evidence],
    }


def _bundle_view(ctx: CloudCtx) -> dict[str, Any]:
    """LLM 에 줄 정규화 증거. 도구 조회분과 같은 내용이며 잘림 시에도 판정에 필요한 전체를 준다."""
    b = ctx.bundle.model_dump()
    b.pop("sources", None)
    return b  # type: ignore[no-any-return]


async def plan_audit(state: CState, config: RunnableConfig) -> dict[str, Any]:
    ctx = _ctx(config)
    if ctx.runner is None:
        return {"plan": {"checks": ["A1", "A4", "B1", "C1", "C2", "C3"], "guidance_queries": [], "missing": []}}
    b = ctx.bundle
    overview = {
        "tools": [t.id for t in b.tools],
        "principals": [p.id for p in b.principals],
        "policies": [p.id for p in b.policies],
        "resources": [r.id for r in b.resources],
        "mappings": len(b.mappings.tool_to_principal),
    }
    out = await ctx.runner.run(
        "cloud_plan",
        PlanOut,
        "plan_audit",
        business_policy=wrap_untrusted(_json(b.business_policy.model_dump()), "business_policy"),
        overview=_json(overview),
    )
    return {"plan": out.model_dump()}


async def _run_calls(ctx: CloudCtx, calls: list[Call]) -> list[dict[str, Any]]:
    got: list[dict[str, Any]] = []
    for c in calls[:4]:  # 한 턴에 4개 호출까지 (여러 ID 를 묶어 조회하도록 유도)
        res = await ctx.host.call(c.tool, c.args)
        got.append({"tool": c.tool, "ok": res.ok, "content": res.content})
    return got


async def collect(state: CState, config: RunnableConfig) -> dict[str, Any]:
    ctx = _ctx(config)
    guidance: list[dict[str, Any]] = list(state.get("guidance", []))
    if ctx.runner is None:
        return {"guidance": guidance}
    for round_ in range(2):  # 초기 collect 최대 2턴
        if round_ == 1 and not ctx.budget.can_collect_more():
            break
        if not ctx.budget.can_collect_more():
            break
        out = await ctx.runner.run(
            "cloud_collect",
            CollectOut,
            "collect",
            plan=_json(state.get("plan", {})),
            have="bundle(tools, principals, policies, mappings, resources)",
            uris=_json(ctx.allowed_uris),
        )
        if out.calls:
            guidance += await _run_calls(ctx, out.calls)
        if out.done or not out.calls:
            break
    return {"guidance": guidance}


def _guidance_text(ctx: CloudCtx, guidance: list[dict[str, Any]]) -> str:
    return wrap_untrusted(_json(guidance), "guidance")[:6000] if guidance else "(없음)"


async def assess(state: CState, config: RunnableConfig) -> dict[str, Any]:
    ctx = _ctx(config)
    if ctx.runner is None:
        rep = audit_bundle(ctx.bundle)
        found = [f for f in rep.findings if f.area != "B1"]
        ctx.partial["findings"] = _dump(found)
        return {"findings": _dump(found), "normal": list(rep.cleared), "not_checked": list(rep.not_checked)}
    if not ctx.budget.can_call_llm():
        return {"not_checked": ["assess: LLM 예산 소진으로 A·C 항목을 점검하지 못함"]}
    out = await ctx.runner.run(
        "cloud_assess",
        AssessOut,
        "assess",
        business_policy=wrap_untrusted(_json(ctx.bundle.business_policy.model_dump()), "business_policy"),
        evidence=_untrusted_evidence(ctx, _bundle_view(ctx)),
        guidance=_guidance_text(ctx, state.get("guidance", [])),
        missing=_json(state.get("plan", {}).get("missing", [])),
    )
    found = [f for f in out.findings if f.area != "B1"]
    ctx.partial["findings"] = _dump(found)
    return {"findings": _dump(found), "normal": out.normal, "not_checked": out.not_checked}


async def build_paths(state: CState, config: RunnableConfig) -> dict[str, Any]:
    ctx = _ctx(config)
    if ctx.runner is None:
        rep = audit_bundle(ctx.bundle)
        paths = [f for f in rep.findings if f.area == "B1"]
        allf = [*state.get("findings", []), *_dump(paths)]
        ctx.partial["findings"] = allf
        return {"findings": allf}
    if not ctx.budget.can_call_llm():
        return {
            "not_checked": [*state.get("not_checked", []), "build_paths: LLM 예산 소진으로 B1 경로를 구성하지 못함"]
        }
    out = await ctx.runner.run(
        "cloud_paths",
        PathOut,
        "build_paths",
        evidence=_untrusted_evidence(ctx, _bundle_view(ctx)),
        business_policy=wrap_untrusted(_json(ctx.bundle.business_policy.model_dump()), "business_policy"),
    )
    allf = [*state.get("findings", []), *_dump([f for f in out.findings if f.area == "B1"])]
    ctx.partial["findings"] = allf
    return {"findings": allf, "normal": [*state.get("normal", []), *out.normal]}


def _known_ids(b: EvidenceBundle) -> set[str]:
    ids: set[str] = set()
    for group in (b.tools, b.principals, b.policies, b.resources):
        for o in group:
            ids |= {o.id, o.source_ref}
    ids |= {x.id for x in b.sources}
    ids |= {m.id for m in b.missing_evidence}
    ids |= {f"map:{m.tool_id}->{m.principal_id}" for m in b.mappings.tool_to_principal}
    ids |= {m.source_ref for m in b.mappings.tool_to_principal}
    return ids


def verify_findings(b: EvidenceBundle, findings: list[CloudFinding]) -> tuple[list[tuple[int, str]], list[str]]:
    """코드 검증: 근거 ID 존재, 자원 존재, B1 연결·정책 존재, 판단 보류 필드. 반환 (오류[(index, 사유)], 경고)."""
    errors: list[tuple[int, str]] = []
    known = _known_ids(b)
    tools = {t.id: t for t in b.tools}
    for i, f in enumerate(findings):
        bad = [e for e in f.evidence_ids if e and e not in known]
        if bad:
            errors.append((i, f"unknown evidence ids {bad}"))
        if not f.evidence_ids:
            errors.append((i, "no evidence cited"))
        if f.status == "HOLD" and not f.needs:
            errors.append((i, "HOLD without needs"))
        if f.area == "B1":
            if len(f.path) != 3 or f.path[0] not in tools:
                errors.append((i, f"invalid path {f.path}"))
                continue
            tool_id, pid, rid = f.path
            if not any(m.tool_id == tool_id and m.principal_id == pid for m in b.mappings.tool_to_principal):
                errors.append((i, f"no explicit mapping {tool_id}->{pid}"))
                continue
            res = b.resource(rid)
            pols = [p for p in b.policies if p.principal_id == pid]
            if res is None or not any(_reachable(p, tools[tool_id], res)[0] for p in pols):
                errors.append((i, f"policy does not allow {pid} on {rid}"))
                continue
            if tools[tool_id].access_policy_ref is None and f.status != "HOLD":
                errors.append((i, "caller access policy missing: B1 must be HOLD"))
    return errors, []


def _dedupe(items: list[CloudFinding]) -> list[CloudFinding]:
    seen: set[tuple[str, str, str]] = set()
    out: list[CloudFinding] = []
    for f in items:
        if f.key not in seen:
            seen.add(f.key)
            out.append(f)
    return out


def _downgrade(
    b: EvidenceBundle, findings: list[CloudFinding], errors: list[tuple[int, str]]
) -> tuple[list[CloudFinding], list[str]]:
    """회귀 후에도 남은 오류: 존재하지 않는 B1 연결은 버리고, 근거 문제는 판단 보류로 낮춘다."""
    bad = dict(errors)
    kept: list[CloudFinding] = []
    notes: list[str] = []
    for i, f in enumerate(findings):
        if i not in bad:
            kept.append(f)
            continue
        reason = bad[i]
        if f.area == "B1" and ("mapping" in reason or "policy does not allow" in reason or "invalid path" in reason):
            notes.append(f"B1 {f.path} 제외: {reason}")
            continue
        known = _known_ids(b)
        f.evidence_ids = [e for e in f.evidence_ids if e in known]
        f.status = "HOLD"
        f.needs = f.needs or [f"검증 실패({reason}): 근거 확인 필요"]
        kept.append(f)
    return kept, notes


async def verify(state: CState, config: RunnableConfig) -> dict[str, Any]:
    ctx = _ctx(config)
    findings = _dedupe(_load(state.get("findings", [])))
    errors, _ = verify_findings(ctx.bundle, findings)
    msgs = [f"{findings[i].area}:{findings[i].resource_id}: {m}" for i, m in errors]
    will_repair = bool(errors) and not state.get("repaired") and ctx.runner is not None and ctx.budget.can_repair()
    if will_repair:
        return {"verification_errors": msgs, "findings": _dump(findings)}
    kept, notes = _downgrade(ctx.bundle, findings, errors) if errors else (findings, [])
    kept = _dedupe(kept)
    ctx.partial["findings"] = _dump(kept)
    return {
        "verification_errors": msgs,
        "findings": _dump(kept),
        "not_checked": [*state.get("not_checked", []), *notes],
    }


def route_verify(state: CState, config: RunnableConfig) -> str:
    ctx = _ctx(config)
    if (
        state.get("verification_errors")
        and not state.get("repaired")
        and ctx.runner is not None
        and ctx.budget.can_repair()
    ):
        return "repair_collect"
    return "draft_report"


async def repair_collect(state: CState, config: RunnableConfig) -> dict[str, Any]:
    ctx = _ctx(config)
    ctx.budget.repair_count += 1
    guidance = list(state.get("guidance", []))
    if ctx.runner is None or not ctx.budget.can_collect_more():
        return {"repaired": True, "guidance": guidance}
    out = await ctx.runner.run(
        "cloud_collect",
        CollectOut,
        "repair_collect",
        plan=_json({"verification_errors": state.get("verification_errors", [])}),
        have="bundle(tools, principals, policies, mappings, resources)",
        uris=_json(ctx.allowed_uris),
    )
    if out.calls:
        guidance += await _run_calls(ctx, out.calls)
    return {"repaired": True, "guidance": guidance}


async def repair_assess_paths(state: CState, config: RunnableConfig) -> dict[str, Any]:
    ctx = _ctx(config)
    if ctx.runner is None or not ctx.budget.can_call_llm():
        return {}
    out = await ctx.runner.run(
        "cloud_repair",
        AssessOut,
        "repair_assess_paths",
        errors=_json(state.get("verification_errors", [])),
        extra=_guidance_text(ctx, state.get("guidance", [])),
        previous=_json(state.get("findings", [])),
        evidence=_untrusted_evidence(ctx, _bundle_view(ctx)),
    )
    ctx.partial["findings"] = _dump(out.findings)
    return {
        "findings": _dump(out.findings),
        "normal": out.normal or state.get("normal", []),
        "not_checked": out.not_checked,
    }


async def draft_report(state: CState, config: RunnableConfig) -> dict[str, Any]:
    ctx = _ctx(config)
    findings = _load(state.get("findings", []))
    if ctx.runner is None or not ctx.budget.can_call_llm():
        counts = {s: sum(1 for f in findings if f.status == s) for s in ("CONFIRMED", "CONDITIONAL", "HOLD")}
        return {"summary": f"발견사항 {len(findings)}건 {counts}", "proposed_action": None}
    out = await ctx.runner.run(
        "cloud_report",
        ReportOut,
        "draft_report",
        business_policy=wrap_untrusted(_json(ctx.bundle.business_policy.model_dump()), "business_policy"),
        findings=_json(state.get("findings", [])),
    )
    action = out.proposed_action.model_dump() if (out.proposed_action and ctx.enable_action) else None
    for i in range(0, len(out.summary), 80):  # 화면 표시용 텍스트 조각 (최종 보고서의 기준은 /report 저장본)
        ctx.emit("TOKEN", {"text": out.summary[i : i + 80]})
    return {"summary": out.summary, "proposed_action": action}


def route_report(state: CState) -> str:
    return "prepare_action" if state.get("proposed_action") else "finalize"


async def prepare_action(state: CState, config: RunnableConfig) -> dict[str, Any]:
    """interrupt 이전 부작용 없음. 고정된 행동 초안·인수 해시·예산 스냅샷을 체크포인트에 남긴다."""
    ctx = _ctx(config)
    args = dict(state.get("proposed_action") or {})
    action = {
        "action_id": f"act-{ctx.task_id}-1",
        "tool": "business_create_remediation_ticket",
        "args": args,
        "args_hash": args_hash(args),
    }
    ctx.budget.pause_clock()
    snap = ctx.budget.snapshot()
    ctx.emit("APPROVAL_REQUIRED", {**action, "active_elapsed_ms": snap["active_elapsed_ms"]})
    return {"action": action, "budget_snapshot": snap}


async def approval(state: CState, config: RunnableConfig) -> dict[str, Any]:
    decision = interrupt({"type": "approval", **(state.get("action") or {})})
    return {"approval": decision if isinstance(decision, dict) else {"decision": decision}}


async def execute_action(state: CState, config: RunnableConfig) -> dict[str, Any]:
    ctx = _ctx(config)
    ctx.budget.resume_clock()
    ap = state.get("approval") or {}
    action = state.get("action") or {}
    if ap.get("decision") != "APPROVE":
        return {"action_result": {"status": "REJECTED"}}
    res = await ctx.host.call(
        "business_create_remediation_ticket",
        {
            "action_id": action["action_id"],
            "args": action["args"],
            "approval_ref": ap.get("approval_ref"),
            "args_hash": action["args_hash"],
        },
    )
    if not res.ok:
        return {"action_result": {"status": "FAILED", "error": res.error_code, "detail": res.content[:300]}}
    return {"action_result": {"status": "CREATED", **json.loads(res.content)}}


def build_sources(ctx: CloudCtx, findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """인용된 근거 ID → {evidence_id, kind, location, excerpt}. 기준 문서는 URI·버전. 모두 마스킹한다."""
    b = ctx.bundle
    where = {s.id: s.location for s in b.sources}
    objs: dict[str, tuple[str, Any]] = {}
    for kind, items in (("tool", b.tools), ("principal", b.principals), ("policy", b.policies), ("resource", b.resources)):
        for o in items:
            objs[o.id] = (kind, o)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for f in findings:
        for eid in f.get("evidence_ids", []):
            if eid in seen:
                continue
            seen.add(eid)
            if eid in objs:
                kind, o = objs[eid]
                out.append(
                    {
                        "evidence_id": eid,
                        "kind": kind,
                        "source_ref": o.source_ref,
                        "location": where.get(o.source_ref, ""),
                        "excerpt": ctx.host.masker.payload(o.model_dump()),
                    }
                )
            else:
                out.append({"evidence_id": eid, "kind": "source" if eid in where else "other", "location": where.get(eid, "")})
    out += ctx.host.guidance_reads
    return out


def build_answer(ctx: CloudCtx, state: CState) -> dict[str, Any]:
    findings = state.get("findings", [])
    return {
        "summary": state.get("summary", ""),
        "findings": findings,
        "normal": state.get("normal", []),
        "notChecked": state.get("not_checked", []),
        "pendingAction": state.get("action") if state.get("action_result") is None else None,
        "actionResult": state.get("action_result"),
        "usage": ctx.budget.snapshot(),
        "llmUsed": ctx.runner is not None,
        "sources": build_sources(ctx, findings),
    }


async def finalize(state: CState, config: RunnableConfig) -> dict[str, Any]:
    ctx = _ctx(config)
    ctx.budget.pause_clock()
    if ctx.on_report is not None:  # 보고서 저장이 성공한 뒤에만 DONE 을 보낸다
        await ctx.on_report(build_answer(ctx, state))
    ctx.emit("DONE", {"usage": ctx.budget.snapshot(), "action_result": state.get("action_result")})
    return {"status": "COMPLETED"}


def build_cloud_graph(checkpointer: BaseCheckpointSaver | None = None) -> CompiledStateGraph:  # type: ignore[type-arg]
    g = StateGraph(CState)
    for name, fn in (
        ("load_evidence", load_evidence),
        ("plan_audit", plan_audit),
        ("collect", collect),
        ("assess", assess),
        ("build_paths", build_paths),
        ("verify", verify),
        ("repair_collect", repair_collect),
        ("repair_assess_paths", repair_assess_paths),
        ("draft_report", draft_report),
        ("prepare_action", prepare_action),
        ("approval", approval),
        ("execute_action", execute_action),
        ("finalize", finalize),
    ):
        g.add_node(name, fn)  # type: ignore[call-overload]
    g.add_edge(START, "load_evidence")
    g.add_edge("load_evidence", "plan_audit")
    g.add_edge("plan_audit", "collect")
    g.add_edge("collect", "assess")
    g.add_edge("assess", "build_paths")
    g.add_edge("build_paths", "verify")
    g.add_conditional_edges("verify", route_verify, ["repair_collect", "draft_report"])
    g.add_edge("repair_collect", "repair_assess_paths")
    g.add_edge("repair_assess_paths", "verify")
    g.add_conditional_edges("draft_report", route_report, ["prepare_action", "finalize"])
    g.add_edge("prepare_action", "approval")
    g.add_edge("approval", "execute_action")
    g.add_edge("execute_action", "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)


# ---------------- A: 단순 조회 루프 ----------------


async def run_single(ctx: CloudCtx) -> CState:
    assert ctx.runner is not None
    ctx.budget.resume_clock()
    obs: list[dict[str, Any]] = []
    bp = wrap_untrusted(_json(ctx.bundle.business_policy.model_dump()), "business_policy")
    final: SingleFinal | None = None
    while final is None and ctx.budget.can_call_llm():
        left = ctx.budget.max_llm_turns - ctx.budget.llm_turns
        out = await ctx.runner.run(
            "cloud_single",
            SingleOut,
            "single",
            business_policy=bp,
            uris=_json(ctx.allowed_uris),
            observations=wrap_untrusted(_json(obs), "tool_results")
            if obs
            else "(없음 — business_get_audit_evidence 로 증거를 먼저 조회한다)",
            turns_left=left,
        )
        if out.final is not None and not (out.calls and left > 1):
            final = out.final
            break
        if out.calls:
            obs += await _run_calls(ctx, out.calls)
        elif left <= 1:
            break
    if final is None:
        return {
            "findings": [],
            "normal": [],
            "not_checked": ["턴 한도 도달: 최종 판정 없음"],
            "summary": "",
            "status": "INCOMPLETE",
        }
    return {
        "findings": _dump(_dedupe(final.findings)),
        "normal": final.normal,
        "not_checked": final.not_checked,
        "summary": final.summary,
        "status": "COMPLETED",
    }


# ---------------- 실행 진입점 ----------------

Config = Literal["A", "B"]


def _result(ctx: CloudCtx, state: dict[str, Any], status: str, failure: dict[str, Any] | None) -> dict[str, Any]:
    ctx.budget.pause_clock()
    return {
        "task_id": ctx.task_id,
        "bundle_id": ctx.bundle.bundle_id,
        "status": status,
        "failure": failure,
        "llm_used": ctx.runner is not None,
        "findings": state.get("findings", ctx.partial.get("findings", [])),
        "normal": state.get("normal", []),
        "not_checked": state.get("not_checked", []),
        "verification_errors": state.get("verification_errors", []),
        "summary": state.get("summary", ""),
        "proposed_action": state.get("proposed_action"),
        "action": state.get("action"),
        "action_result": state.get("action_result"),
        "usage": ctx.budget.snapshot(),
        "events": ctx.events,
        "tool_log": [e.__dict__ for e in ctx.host.log],
    }


async def _drive(graph: CompiledStateGraph, inp: Any, rc: RunnableConfig, ctx: CloudCtx) -> dict[str, Any]:  # type: ignore[type-arg]
    """stream 으로 실행하며 노드 사이마다 이벤트를 flush 한다. 마지막 values 청크(및 interrupt 표식)를 반환."""
    last: dict[str, Any] = {}
    interrupted = False
    async for chunk in graph.astream(inp, rc, stream_mode="values"):
        if "__interrupt__" in chunk:
            interrupted = True
        else:
            last = dict(chunk)
        await ctx.flush()
    if interrupted:
        last["__interrupt__"] = True
    return last


async def run_cloud_audit(
    ctx: CloudCtx, config_kind: Config = "B", checkpointer: BaseCheckpointSaver | None = None
) -> dict[str, Any]:
    """A/B 실행. 예산 소진(BUDGET_EXCEEDED)은 부분 결과 + FAILED 코드로 반환한다 (새 호출 없음)."""
    try:
        if config_kind == "A":
            state: dict[str, Any] = dict(await run_single(ctx))
            await ctx.flush()
            return _result(ctx, state, state.get("status", "COMPLETED"), None)
        graph = build_cloud_graph(checkpointer)
        rc: RunnableConfig = {"configurable": {"ctx": ctx, "thread_id": ctx.task_id}, "recursion_limit": 60}
        gstate = await _drive(graph, {}, rc, ctx)
        interrupted = bool(gstate.get("__interrupt__"))
        return _result(ctx, gstate, "WAITING_APPROVAL" if interrupted else gstate.get("status", "COMPLETED"), None)
    except AppError as e:
        code = (e.details or {}).get("code") if e.code == ErrorCode.BUDGET_EXCEEDED else str(e.code)
        ctx.emit("FAILED", {"code": code, "resumable": False})
        await ctx.flush()
        return _result(ctx, {}, "FAILED", {"code": code, "message": e.message})


async def resume_cloud_audit(
    ctx: CloudCtx, decision: dict[str, Any], checkpointer: BaseCheckpointSaver
) -> dict[str, Any]:
    """같은 thread_id 로 Command(resume=...) 재개. 누적 예산은 체크포인트의 스냅샷에서 복원한다."""
    graph = build_cloud_graph(checkpointer)
    rc: RunnableConfig = {"configurable": {"ctx": ctx, "thread_id": ctx.task_id}, "recursion_limit": 60}
    snap = await graph.aget_state(rc)
    saved = snap.values.get("budget_snapshot") if snap and snap.values else None
    if saved:
        ctx.budget.restore(saved)
    try:
        rstate = await _drive(graph, Command(resume=decision), rc, ctx)
    except AppError as e:
        code = (e.details or {}).get("code") if e.code == ErrorCode.BUDGET_EXCEEDED else str(e.code)
        ctx.emit("FAILED", {"code": code, "resumable": False})
        await ctx.flush()
        return _result(ctx, {}, "FAILED", {"code": code, "message": e.message})
    return _result(ctx, rstate, rstate.get("status", "COMPLETED"), None)
