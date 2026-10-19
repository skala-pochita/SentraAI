from __future__ import annotations

import copy
from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver

from core.cloud.budget import CloudBudget
from core.cloud.pipeline import resume_cloud_audit, run_cloud_audit
from core.cloud.tickets import ApprovalRecord
from tests.fakes_cloud import RateLimitError, ScriptedModel, finding, make_ctx
from tests.unit.test_cloud_checks import BASE

PLAN = {"checks": ["B1"], "guidance_queries": [], "missing": []}
COLLECT = {"calls": [{"tool": "rag_search_security_guidance", "args": {"query": "IAM 최소 권한"}}], "done": True}
ASSESS = {"findings": [], "normal": ["no C findings"], "not_checked": []}
REPORT = {"summary": "요약", "proposed_action": None}


def happy_script(paths: list[dict[str, Any]] | None = None, report: dict[str, Any] | None = None) -> list[Any]:
    return [
        PLAN,
        COLLECT,
        ASSESS,
        {"findings": paths if paths is not None else [finding()], "normal": []},
        report or REPORT,
    ]


async def test_b_happy_path_uses_five_turns_and_tools() -> None:
    model = ScriptedModel(happy_script())
    ctx = make_ctx(model)
    res = await run_cloud_audit(ctx, "B")
    assert res["status"] == "COMPLETED" and res["llm_used"]
    assert res["usage"]["llm_turns"] == 5  # plan 1 + collect 1 + assess 1 + paths 1 + report 1
    assert [f["area"] for f in res["findings"]] == ["B1"]
    names = [e["tool"] for e in res["tool_log"]]
    assert names == ["business_get_audit_evidence", "rag_search_security_guidance"]
    types = [e["type"] for e in res["events"]]
    assert types.count("TOOL_START") == 2 and types[-1] == "DONE"


async def test_verify_triggers_one_repair_then_downgrades() -> None:
    bad = finding(evidence_ids=["does-not-exist"])
    repaired_still_bad = {"findings": [bad], "normal": [], "not_checked": []}
    model = ScriptedModel(
        [PLAN, COLLECT, ASSESS, {"findings": [bad], "normal": []}, COLLECT, repaired_still_bad, REPORT]
    )
    ctx = make_ctx(model)
    res = await run_cloud_audit(ctx, "B")
    assert res["usage"]["repair_count"] == 1
    assert res["usage"]["llm_turns"] == 7  # plan·collect·assess·paths + repair 2 + report
    (f,) = res["findings"]
    assert f["status"] == "HOLD" and f["needs"]  # 회귀 뒤에도 오류면 판단 보류


async def test_invalid_b1_path_is_dropped_not_invented() -> None:
    invented = finding(path=["T1", "R-CALLER", "bucket-customers"])  # 매핑에 없는 연결
    model = ScriptedModel([PLAN, COLLECT, ASSESS, {"findings": [invented]}, COLLECT, {"findings": []}, REPORT])
    res = await run_cloud_audit(make_ctx(model), "B")
    assert res["findings"] == [] and res["usage"]["repair_count"] == 1

    # 회귀 후에도 같은 가짜 경로를 내면 버리고 not_checked 에 사유를 남긴다
    model2 = ScriptedModel([PLAN, COLLECT, ASSESS, {"findings": [invented]}, COLLECT, {"findings": [invented]}, REPORT])
    res2 = await run_cloud_audit(make_ctx(model2), "B")
    assert res2["findings"] == [] and any("제외" in n for n in res2["not_checked"])


async def test_b1_without_caller_policy_must_hold() -> None:
    d = copy.deepcopy(BASE)
    d["tools"][0]["access_policy_ref"] = None
    confirmed = finding()  # CONFIRMED 인데 호출자 정책 없음 → 검증 오류
    held = finding(status="HOLD", needs=["tool:T1 caller access policy"])
    model = ScriptedModel([PLAN, COLLECT, ASSESS, {"findings": [confirmed]}, COLLECT, {"findings": [held]}, REPORT])
    res = await run_cloud_audit(make_ctx(model, d), "B")
    (f,) = res["findings"]
    assert f["status"] == "HOLD" and res["usage"]["repair_count"] == 1


async def test_turn_limit_is_never_exceeded() -> None:
    model = ScriptedModel([{"calls": [{"tool": "business_get_audit_evidence", "args": {}}], "final": None}])
    ctx = make_ctx(model)
    res = await run_cloud_audit(ctx, "A")
    assert res["usage"]["llm_turns"] <= 8
    assert res["status"] == "INCOMPLETE"


async def test_same_tool_limit_three() -> None:
    ctx = make_ctx(None)
    results = [await ctx.host.call("rag_search_security_guidance", {"query": "iam"}) for _ in range(4)]
    assert [r.ok for r in results] == [True, True, True, False]
    assert results[3].error_code == "BUDGET_EXCEEDED"


async def test_read_resource_allowlist_and_limit() -> None:
    ctx = make_ctx(None)
    ok = await ctx.host.call("read_resource", {"uri": "guidance://security-baseline/v1"})
    bad = await ctx.host.call("read_resource", {"uri": "guidance://answers/v1"})
    assert ok.ok and bad.error_code == "FORBIDDEN"
    assert bad.content.count("allowed") == 1


async def test_unregistered_tool_blocked_and_logged() -> None:
    ctx = make_ctx(None)
    r = await ctx.host.call("business_delete_everything", {})
    assert not r.ok and ctx.host.log[-1].requested_only


async def test_tool_output_masked_and_truncated() -> None:
    d = copy.deepcopy(BASE)
    d["tools"][0]["description"] = "문의: kim@example.com 010-1234-5678 key=sk-abcdefghijklmnopqrstuvwxyz"
    ctx = make_ctx(None, d)
    r = await ctx.host.call("business_get_audit_evidence", {"evidence_ids": ["T1"]})
    assert "kim@example.com" not in r.content and "010-1234-5678" not in r.content
    assert "sk-abcdefghij" not in r.content and "<email-1>" in r.content
    ctx.host.max_chars = 50
    r2 = await ctx.host.call("business_get_audit_evidence", {})
    assert r2.truncated


async def test_429_retry_max_two_then_fail() -> None:
    ok = ScriptedModel([RateLimitError(), RateLimitError(), PLAN])
    ctx = make_ctx(ok)
    ctx.runner.model.backoff = [0.0, 0.0]  # type: ignore[union-attr]
    out = await ctx.runner.run(
        "cloud_plan",
        __import__("core.cloud.pipeline", fromlist=["PlanOut"]).PlanOut,
        "plan_audit",
        business_policy="x",
        overview="y",
    )  # type: ignore[union-attr]
    assert out.checks == ["B1"] and ctx.budget.provider_request_count == 3 and ctx.budget.llm_turns == 1

    bad = ScriptedModel([RateLimitError()])
    ctx2 = make_ctx(bad)
    ctx2.runner.model.backoff = [0.0, 0.0]  # type: ignore[union-attr]
    res = await run_cloud_audit(ctx2, "B")
    assert res["status"] == "FAILED" and ctx2.budget.provider_request_count == 3


async def test_time_budget_exhausted_returns_partial_failed() -> None:
    model = ScriptedModel(happy_script())
    ctx = make_ctx(model)
    ctx.budget.max_active_s = 0.0
    res = await run_cloud_audit(ctx, "B")
    assert res["status"] == "FAILED" and res["failure"]["code"] == "TIME_BUDGET_EXCEEDED"
    assert ctx.budget.llm_turns == 0  # 새 모델 호출 없음


def action_report() -> dict[str, Any]:
    return {"summary": "s", "proposed_action": {"title": "S3 권한 축소", "target": "platform-team", "body": "b"}}


async def test_approval_interrupt_resume_creates_exactly_one_ticket() -> None:
    saver = MemorySaver()
    model = ScriptedModel(happy_script(report=action_report()))
    ctx = make_ctx(model, enable_action=True)
    res = await run_cloud_audit(ctx, "B", saver)
    assert res["status"] == "WAITING_APPROVAL" and res["action"]["args"]["target"] == "platform-team"
    assert not ctx.host.backend.tickets.tickets  # type: ignore[attr-defined]
    # 승인 전 직접 호출해도 차단
    direct = await ctx.host.call(
        "business_create_remediation_ticket", {"action_id": res["action"]["action_id"], "args": res["action"]["args"]}
    )
    assert direct.error_code == "FORBIDDEN"

    store = ctx.host.backend.tickets  # type: ignore[attr-defined]
    act = res["action"]
    store.record_approval(ApprovalRecord("AP-1", ctx.task_id, act["action_id"], act["args_hash"], "APPROVED"))
    # 새 컨텍스트/예산(프로세스 재시작 모사)으로 같은 thread_id 재개 — 모델 호출 없이 이어서 실행
    ctx2 = make_ctx(ScriptedModel([PLAN]), task_id=ctx.task_id, enable_action=True)
    ctx2.host.backend = ctx.host.backend
    out = await resume_cloud_audit(ctx2, {"decision": "APPROVE", "approval_ref": "AP-1"}, saver)
    assert out["status"] == "COMPLETED" and out["action_result"]["status"] == "CREATED"
    assert ctx2.budget.llm_turns == 5  # 누적 한도 이어받음 (초기화 아님)
    assert len(store.tickets) == 1

    again = store.create(ctx.task_id, act["action_id"], act["args"], "AP-1")
    assert again["duplicate"] and len(store.tickets) == 1
    with pytest.raises(Exception, match="different arguments"):
        store.create(ctx.task_id, act["action_id"], {**act["args"], "target": "attacker"}, "AP-1")


async def test_rejected_or_changed_args_do_not_execute() -> None:
    saver = MemorySaver()
    ctx = make_ctx(ScriptedModel(happy_script(report=action_report())), enable_action=True, task_id="t2")
    await run_cloud_audit(ctx, "B", saver)
    out = await resume_cloud_audit(ctx, {"decision": "REJECT"}, saver)
    assert out["action_result"]["status"] == "REJECTED" and out["status"] == "COMPLETED"

    saver2 = MemorySaver()
    ctx3 = make_ctx(ScriptedModel(happy_script(report=action_report())), enable_action=True, task_id="t3")
    res3 = await run_cloud_audit(ctx3, "B", saver2)
    act = res3["action"]
    ctx3.host.backend.tickets.record_approval(  # type: ignore[attr-defined]
        ApprovalRecord("AP-9", "t3", act["action_id"], "0" * 64, "APPROVED")  # 승인된 해시가 다름
    )
    out3 = await resume_cloud_audit(ctx3, {"decision": "APPROVE", "approval_ref": "AP-9"}, saver2)
    assert out3["action_result"]["status"] == "FAILED" and out3["action_result"]["error"] == "FORBIDDEN"
    assert not ctx3.host.backend.tickets.tickets  # type: ignore[attr-defined]


async def test_offline_b_without_llm_uses_deterministic_checks() -> None:
    res = await run_cloud_audit(make_ctx(None), "B")
    assert res["llm_used"] is False and any(f["area"] == "B1" for f in res["findings"])
    assert res["usage"]["llm_turns"] == 0


def test_budget_clock_excludes_pause_and_restores() -> None:
    b = CloudBudget.from_config()
    b.resume_clock()
    b.pause_clock()
    snap = b.snapshot()
    b2 = CloudBudget.from_config()
    b2.restore({**snap, "llm_turns": 4, "tool_calls": {"x": 2}})
    assert b2.llm_turns == 4 and b2.tool_calls["x"] == 2


async def test_ticket_target_is_pinned_to_policy_not_model() -> None:
    evil = {"summary": "s", "proposed_action": {"title": "x", "target": "attacker@evil.example", "body": "b"}}
    ctx = make_ctx(ScriptedModel(happy_script(report=evil)), enable_action=True)
    res = await run_cloud_audit(ctx, "B", MemorySaver())
    assert res["action"]["args"]["target"] == "platform-team"
