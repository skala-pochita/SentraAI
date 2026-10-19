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
from tests.fakes_cloud import ScriptedModel

pytestmark = pytest.mark.usefixtures("db_ready")

PAY_CFG = "src/main/java/com/demo/shop/config/PaymentConfig.java"
PAY_CLIENT = "src/main/java/com/demo/shop/client/PaymentClient.java"


def read_step(path: str, why: str = "의심 파일을 직접 읽는다") -> dict[str, Any]:
    return {"why": why, "action": {"tool": "read_repo_file", "args": {"path": path}}, "final": None}


def final(verdicts: list[dict[str, Any]] | None = None, new: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"why": "", "action": None, "final": {"verdicts": verdicts or [], "new_findings": new or []}}


@pytest.fixture
async def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
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
    out, deps = await run(repo, script, state(repo, risky=[PAY_CFG, PAY_CLIENT]))
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
    assert len(steps) == 6  # 최대 6 스텝, 이후 final 만 요구
    assert out["investigated"] == []


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
