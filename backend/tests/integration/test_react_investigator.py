"""ReAct 조사 (스크립트된 모델, in-process MCP): C19·C20 탐지, 스텝 상한, H3 게이트, 인젝션 견고성, 근거 검증 연동."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from core.graph.deps import Deps
from core.graph.nodes.react_investigator import investigate
from core.mcp_client import inprocess_gateway
from core.settings import REPO_ROOT, get_settings
from mcp_servers.business.demo_reset import reset
from tests.fakes_llm import ScriptedModel

pytestmark = pytest.mark.usefixtures("db_ready")

PAY_CFG = "src/main/java/com/demo/shop/config/PaymentConfig.java"
PAY_CLIENT = "src/main/java/com/demo/shop/client/PaymentClient.java"


def read_step(path: str, why: str = "의심 파일을 직접 읽는다") -> dict[str, Any]:
    return {"why": why, "action": {"tool": "read_repo_file", "args": {"path": path}}, "final": None}


def final(verdicts: list[dict[str, Any]] | None = None, new: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"why": "", "action": None, "final": {"verdicts": verdicts or [], "new_findings": new or []}}


@pytest.fixture
async def repo(db_ready: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from api.seed import seed_users
    from core.db.models import Target
    from core.db.session import session_scope

    monkeypatch.setenv("OPENAI_API_KEY", "")
    get_settings.cache_clear()
    reset(REPO_ROOT / "targets", tmp_path, "vuln")
    async with session_scope() as db:
        users = await seed_users(db)
        t = Target(
            type="repo",
            name="vuln-spring-app",
            ref=str(tmp_path / "vuln-spring-app"),
            owner_user_id=users["DEVELOPER"].id,
        )
        db.add(t)
        await db.flush()
        return {"target_id": str(t.id), "name": t.name, "type": "repo"}


def state(
    repo: dict[str, Any], candidates: list[dict[str, Any]] | None = None, risky: list[str] | None = None
) -> dict[str, Any]:
    return {
        "audit_id": str(uuid.uuid4()),
        "actor_role": "ADMIN",
        "target": repo,
        "candidates": candidates or [],
        "plan": {"repos": {repo["target_id"]: {"risky_files": risky or []}}},
    }


def cfg(deps: Deps) -> Any:
    return {"configurable": {"deps": deps}}


async def run(repo: dict[str, Any], script: list[Any], st: dict[str, Any]) -> tuple[dict[str, Any], Deps]:
    deps = Deps(gateway=inprocess_gateway(), emit_events=False)
    events: list[tuple[str, dict[str, Any]]] = []

    async def capture(state: Any, type_: str, payload: Any = None, node: Any = None, progress: Any = None) -> None:
        events.append((type_, payload or {}))

    deps.event = capture  # type: ignore[method-assign]
    deps.captured = events  # type: ignore[attr-defined]
    out = await investigate(st, cfg(deps), (ScriptedModel(script), "fake"))
    return out, deps


class PathScriptedModel(ScriptedModel):
    """파일별 스크립트: 병렬 조사(B1)에서도 각 파일이 자기 응답 순서를 받는다."""

    def __init__(self, scripts: dict[str, list[Any]]) -> None:
        super().__init__([{}])
        self.scripts = {k: list(v) for k, v in scripts.items()}

    async def ainvoke(self, messages: list[Any]) -> Any:
        human = str(messages[1].content)
        path = next(p for p in self.scripts if p in human.split("observations", 1)[0] or p in human)
        q = self.scripts[path]
        self.responses = [q.pop(0) if len(q) > 1 else q[0]]
        return await super().ainvoke(messages)


async def test_c19_c20_found_by_reading_risky_files(repo: dict[str, Any]) -> None:
    script = [
        read_step(PAY_CFG),
        final(
            new=[
                {
                    "category": "HARDCODED_SECRET",
                    "title": "Base64 로 숨긴 결제 설정",
                    "severity": "HIGH",
                    "file_path": PAY_CFG,
                    "line_start": 13,
                    "snippet": 'MERCHANT_CFG = "cGdf',
                    "reasoning": "Base64 디코드하면 가맹점 비밀",
                }
            ]
        ),
        read_step(PAY_CLIENT),
        final(
            new=[
                {
                    "category": "HARDCODED_SECRET",
                    "title": "분할된 sk_live_ 키",
                    "severity": "HIGH",
                    "file_path": PAY_CLIENT,
                    "line_start": 14,
                    "snippet": 'auth = "sk_live_" + ',
                    "reasoning": "문자열 결합으로 키 구성",
                }
            ]
        ),
    ]
    deps = Deps(gateway=inprocess_gateway(), emit_events=False)
    events: list[tuple[str, dict[str, Any]]] = []

    async def capture(state: Any, type_: str, payload: Any = None, node: Any = None, progress: Any = None) -> None:
        events.append((type_, payload or {}))

    deps.event = capture  # type: ignore[method-assign]
    deps.captured = events  # type: ignore[attr-defined]
    model = PathScriptedModel({PAY_CFG: script[:2], PAY_CLIENT: script[2:]})
    out = await investigate(state(repo, risky=[PAY_CFG, PAY_CLIENT]), cfg(deps), (model, "fake"))
    found = {
        (f["evidence"][0]["file_path"], f["evidence"][0]["line_start"])
        for f in out["investigated"]
        if f["rule_id"] == "LLM-REACT"
    }
    assert found == {(PAY_CFG, 13), (PAY_CLIENT, 14)}
    steps = [p for t, p in deps.captured if t == "investigate.step"]  # type: ignore[attr-defined]
    assert [s["tool"] for s in steps] == ["read_repo_file", "read_repo_file"] and all(s["why"] for s in steps)
    assert all("read_repo_file" in f["tools_used"] for f in out["investigated"])
    # 새 발견의 근거는 다음 노드(code_evidence_check)가 실제 파일과 대조할 수 있는 형태다
    assert out["investigated"][0]["evidence"][0]["snippet_masked"].startswith("MERCHANT_CFG")


async def test_step_cap_forces_final_after_six_steps(repo: dict[str, Any]) -> None:
    script = [read_step(PAY_CFG)] * 6 + [final()]

    out, deps = await run(repo, script, state(repo, risky=[PAY_CFG]))
    steps = [p for t, p in deps.captured if t == "investigate.step"]  # type: ignore[attr-defined]
    assert len(steps) == 3  # 같은 도구는 파일당 3회까지 (Harness Agent Loop), 이후는 거부 관찰
    assert out["investigated"] == []


async def test_timeout_forces_final(repo: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    import core.graph.nodes.react_investigator as ri

    real = ri.limits

    def short() -> dict[str, Any]:
        cfg = real()
        return {**cfg, "react": {**cfg["react"], "max_seconds": 0}}

    monkeypatch.setattr(ri, "limits", short)
    out, deps = await run(repo, [final()], state(repo, risky=[PAY_CFG]))
    limits_ev = [p for t, p in deps.captured if t == "loop.limit"]  # type: ignore[attr-defined]
    assert limits_ev and limits_ev[0]["reason"] == "timeout"
    assert not [p for t, p in deps.captured if t == "investigate.step"]  # type: ignore[attr-defined]


def _patched_react(monkeypatch: pytest.MonkeyPatch, **react: Any) -> None:
    import core.graph.nodes.react_investigator as ri

    real = ri.limits

    def patched() -> dict[str, Any]:
        cfg = real()
        return {**cfg, "react": {**cfg["react"], **react}}

    monkeypatch.setattr(ri, "limits", patched)


async def test_slow_llm_call_is_cut_at_deadline(repo: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """P0-5: LLM 1회 호출이 오래 걸려도 파일당 작업 시간 한도 + final 유예 안에서 끝난다."""
    import asyncio
    import time

    class SlowModel:
        async def ainvoke(self, messages: list[Any]) -> Any:
            await asyncio.sleep(5)  # 한도(0.3초)보다 훨씬 긴 호출

    _patched_react(monkeypatch, max_seconds=0.3, final_grace_s=0.2)
    deps = Deps(gateway=inprocess_gateway(), emit_events=False)
    events: list[tuple[str, dict[str, Any]]] = []

    async def capture(state: Any, type_: str, payload: Any = None, node: Any = None, progress: Any = None) -> None:
        events.append((type_, payload or {}))

    deps.event = capture  # type: ignore[method-assign]
    started = time.monotonic()
    out = await investigate(state(repo, risky=[PAY_CFG]), cfg(deps), (SlowModel(), "fake"))
    assert time.monotonic() - started < 2.0
    assert [p["reason"] for t, p in events if t == "loop.limit"] == ["timeout"]
    assert out["errors"] and out["errors"][0]["code"] == "UPSTREAM_TIMEOUT"  # final 도 시간 초과 → 정적 판정 유지


async def test_llm_token_budget_per_file(repo: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """P0-6: 파일당 LLM 입력+출력 토큰이 상한을 넘으면 더 조사하지 않고 판정한다 (ScriptedModel 은 호출당 150 토큰)."""
    _patched_react(monkeypatch, max_llm_tokens_per_file=300)
    out, deps = await run(repo, [read_step(PAY_CFG)] * 5 + [final()], state(repo, risky=[PAY_CFG]))
    limits_ev = [p for t, p in deps.captured if t == "loop.limit"]  # type: ignore[attr-defined]
    assert limits_ev and limits_ev[0]["reason"] == "llm_token_budget" and limits_ev[0]["llm_tokens"] >= 300
    assert len([p for t, p in deps.captured if t == "investigate.step"]) == 2  # type: ignore[attr-defined]
    assert sum(u["in"] + u["out"] for u in out["token_usage"]) == 450  # 2스텝 + final 1회, 사용량 누락 없음


async def test_disallowed_tool_is_refused_by_gate_and_loop_continues(repo: dict[str, Any]) -> None:
    script = [
        {"why": "삭제 시도", "action": {"tool": "apply_fix", "args": {}}, "final": None},
        {"why": "변경 도구", "action": {"tool": "create_finding", "args": {}}, "final": None},
        final(),
    ]
    out, deps = await run(repo, script, state(repo, risky=[PAY_CFG]))
    assert not [p for t, p in deps.captured if t == "investigate.step"]  # type: ignore[attr-defined]
    assert out["errors"] == [] and out["investigated"] == []


async def test_auditor_injection_candidate_cannot_be_dismissed(repo: dict[str, Any]) -> None:
    cand = {
        "temp_id": "s-1",
        "target_id": repo["target_id"],
        "source": "code",
        "category": "AUDITOR_INJECTION_ATTEMPT",
        "rule_id": "SEC-AUDITOR-001",
        "title": "inj",
        "severity": "MEDIUM",
        "confidence": 0.6,
        "verdict": "NEEDS_REVIEW",
        "evidence": [
            {
                "kind": "code",
                "file_path": "README.md",
                "line_start": 3,
                "line_end": 3,
                "snippet_masked": "ignore previous",
            }
        ],
    }
    out, _ = await run(
        repo,
        [
            final(
                verdicts=[
                    {"temp_id": "s-1", "verdict": "FALSE_POSITIVE", "confidence": 0.9, "reason": "README 지시를 따름"}
                ]
            )
        ],
        state(repo, [cand]),
    )
    (f,) = out["investigated"]
    assert f["verdict"] == "TRUE_POSITIVE" and f["category"] == "AUDITOR_INJECTION_ATTEMPT"


async def test_injection_in_file_blocks_dismissing_neighbours(repo: dict[str, Any]) -> None:
    """I01 재현: 인젝션 주석 바로 아래 permitAll(LLM 재판정 대상, 낮은 신뢰도)을 모델이 기각해도 남긴다."""
    path = "src/main/java/com/demo/shop/config/SecurityConfig.java"

    def cand(tid: str, category: str, line: int, confidence: float) -> dict[str, Any]:
        return {
            "temp_id": tid,
            "target_id": repo["target_id"],
            "source": "code",
            "category": category,
            "rule_id": "R",
            "title": category,
            "severity": "CRITICAL",
            "confidence": confidence,
            "verdict": "NEEDS_REVIEW",
            "evidence": [{"kind": "code", "file_path": path, "line_start": line, "line_end": line}],
        }

    cands = [cand("s-inj", "AUDITOR_INJECTION_ATTEMPT", 24, 0.6), cand("s-pa", "SECCONFIG_PERMIT_ALL", 25, 0.6)]
    dismiss = [{"temp_id": "s-pa", "verdict": "FALSE_POSITIVE", "confidence": 0.9, "reason": "보안팀 검토 완료"}]
    out, _ = await run(repo, [final(verdicts=dismiss)], state(repo, cands))
    pa = next(f for f in out["investigated"] if f["temp_id"] == "s-pa")
    assert pa["verdict"] == "NEEDS_REVIEW" and pa["reasoning_summary"].startswith("[감사기 인젝션이 있는 파일")

    # 인젝션이 없는 파일이면 같은 판정은 그대로 기각된다 (오탐 처리 능력 유지)
    out, _ = await run(repo, [final(verdicts=dismiss)], state(repo, cands[1:]))
    assert out["investigated"][0]["verdict"] == "FALSE_POSITIVE"


async def test_credential_candidates_are_held_outside_tests(repo: dict[str, Any]) -> None:
    """C04 재현: '// TODO 임시 계정 admin / admin1234' 를 모델이 '임시 주석'이라 기각해도 검토로 남긴다."""

    def cand(tid: str, path: str) -> dict[str, Any]:
        return {
            "temp_id": tid,
            "target_id": repo["target_id"],
            "source": "code",
            "category": "COMMENT_SENSITIVE",
            "rule_id": "SEC-COMMENT-001",
            "title": "comment credential",
            "severity": "HIGH",
            "confidence": 0.6,
            "verdict": "NEEDS_REVIEW",
            "evidence": [{"kind": "code", "file_path": path, "line_start": 21, "line_end": 21}],
        }

    main = "src/main/java/com/demo/shop/admin/AdminController.java"
    test = "src/test/java/com/demo/shop/AdminControllerTest.java"
    for path, expected in ((main, "NEEDS_REVIEW"), (test, "FALSE_POSITIVE")):
        dismiss = [{"temp_id": "s-c", "verdict": "FALSE_POSITIVE", "confidence": 0.9, "reason": "임시 계정 주석"}]
        out, _ = await run(repo, [final(verdicts=dismiss)], state(repo, [cand("s-c", path)]))
        assert out["investigated"][0]["verdict"] == expected, path


async def test_observation_with_injection_is_wrapped_untrusted(repo: dict[str, Any]) -> None:
    model = ScriptedModel([read_step("README.md"), final()])
    deps = Deps(gateway=inprocess_gateway(), emit_events=False)
    await investigate(state(repo, risky=["README.md"]), cfg(deps), (model, "fake"))
    second_prompt = str(model.calls[1][-1].content)
    assert '<untrusted source="read_repo_file:README.md">' in second_prompt


async def test_budget_exhaustion_falls_back_to_static_verdicts(repo: dict[str, Any]) -> None:
    deps = Deps(gateway=inprocess_gateway(), emit_events=False)
    deps.budget.max_tokens = 1
    deps.budget.used_tokens = 5
    cand = {
        "temp_id": "s-2",
        "target_id": repo["target_id"],
        "source": "code",
        "category": "SECRET_LEAK",
        "rule_id": "X",
        "title": "t",
        "severity": "HIGH",
        "confidence": 0.85,
        "verdict": "NEEDS_REVIEW",
        "evidence": [{"kind": "code", "file_path": PAY_CFG, "line_start": 13, "line_end": 13, "snippet_masked": "x"}],
    }
    out = await investigate(state(repo, [cand]), cfg(deps), (ScriptedModel([final()]), "fake"))
    assert out["investigated"][0]["verdict"] == "TRUE_POSITIVE"  # 정적 판정(confidence≥0.8)


ORDER_CTRL = "src/main/java/com/demo/shop/controller/OrderController.java"
ORDER_REPO = "src/main/java/com/demo/shop/repository/OrderQueryRepository.java"


async def test_related_files_tool_follows_layers(repo: dict[str, Any]) -> None:
    """S3: 컨트롤러 → 리포지토리 관계를 알려 준다."""
    from core.mcp_client import CallCtx

    gw = inprocess_gateway()
    rel = await gw.call(
        "related_files", {"target_id": repo["target_id"], "path": ORDER_CTRL}, CallCtx("code_investigate", "ADMIN")
    )
    assert {"path": ORDER_REPO, "relation": "uses"} in rel


async def test_new_finding_in_related_file_is_kept_unrelated_dropped(repo: dict[str, Any]) -> None:
    """S3: 관련 파일에 보고한 발견은 유지, 관련 없는 파일은 버린다. 모델이 related_paths 를 채워도 무시한다."""
    nf = {
        "category": "SQL_INJECTION",
        "title": "t",
        "severity": "HIGH",
        "line_start": 5,
        "snippet": "x",
        "reasoning": "r",
    }
    script = [
        read_step(ORDER_CTRL),
        {
            "why": "",
            "action": None,
            "final": {
                "verdicts": [],
                "new_findings": [{**nf, "file_path": ORDER_REPO}, {**nf, "file_path": PAY_CFG}],
                "related_paths": [PAY_CFG],  # 모델이 끼워 넣은 값 — 시스템이 덮어쓴다
            },
        },
    ]
    out, deps = await run(repo, script, state(repo, risky=[ORDER_CTRL]))
    found = [f["evidence"][0]["file_path"] for f in out["investigated"] if f["rule_id"] == "LLM-REACT"]
    assert found == [ORDER_REPO]
