"""Phase 5·6 그래프 경로 (LLM 없음, in-process MCP, 실제 Postgres, fakeredis, InMemorySaver).

- 읽기 전용 감사: intake → code/agent 서브그래프 → correlate → report, 원본 파일 변경 없음
- 퇴역한 수정·승인·병합 도구는 MCP 에 없고 게이트에서 FORBIDDEN
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import select

from api.seed import seed_users
from core.db.models import Approval, Audit, AuditTarget, Finding, FixProposalRow, Report, Target
from core.db.session import session_scope
from core.graph.build import build_graph
from core.graph.deps import Deps
from core.mcp_client import CallCtx, inprocess_gateway
from core.settings import REPO_ROOT
from mcp_servers.business.demo_reset import reset
from mcp_servers.business.tools import execute
from tests.fakes import GullibleAgent


@pytest.fixture
async def env(db_ready: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict[str, Any]]:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("PROBE_ATTEMPTS", "1")
    monkeypatch.setenv("PROBE_ALLOWLIST", "http://target-agent:8201")
    from core.settings import get_settings

    get_settings.cache_clear()
    reset(REPO_ROOT / "targets", tmp_path, "vuln")
    agent_sim = GullibleAgent(tmp_path / "agent-config")

    async def fake_chat(client: Any, url: str, sid: str, message: str, kb: Any) -> dict[str, Any]:
        return agent_sim.chat(message, kb)

    monkeypatch.setattr(execute, "_chat", fake_chat)
    async with session_scope() as db:
        users = await seed_users(db)
        repo = Target(
            type="repo",
            name="vuln-spring-app",
            ref=str(tmp_path / "vuln-spring-app"),
            owner_user_id=users["DEVELOPER"].id,
        )
        db.add(repo)
        await db.flush()
        agent = Target(
            type="agent",
            name="cs-helper",
            ref="http://target-agent:8201",
            linked_target_id=repo.id,
            config={"config_dir": str(tmp_path / "agent-config")},
        )
        db.add(agent)
        await db.flush()
        repo.linked_target_id = agent.id
        admin = users["ADMIN"]
        audit = Audit(requested_by=admin.id, mode="full", status="RUNNING", thread_id="t")
        db.add(audit)
        await db.flush()
        db.add_all(
            [AuditTarget(audit_id=audit.id, target_id=repo.id), AuditTarget(audit_id=audit.id, target_id=agent.id)]
        )
        ids = {"admin": str(admin.id), "repo": str(repo.id), "agent": str(agent.id), "audit": str(audit.id)}
    yield {**ids, "workspace": tmp_path}
    get_settings.cache_clear()


def initial(env: dict[str, Any]) -> dict[str, Any]:
    return {
        "audit_id": env["audit"],
        "thread_id": f"audit:{env['audit']}",
        "requested_by": env["admin"],
        "actor_role": "ADMIN",
        "mode": "full",
        "eval_config": None,
        "targets": [{"target_id": env["repo"]}, {"target_id": env["agent"]}],
    }


def cfg(env: dict[str, Any]) -> dict[str, Any]:
    return {
        "configurable": {"thread_id": f"audit:{env['audit']}", "deps": Deps(gateway=inprocess_gateway())},
        "recursion_limit": 100,
    }


async def test_read_only_full_audit_reports_without_mutation(env: dict[str, Any]) -> None:
    workspace = env["workspace"]
    before = {str(p.relative_to(workspace)): p.read_bytes() for p in workspace.rglob("*") if p.is_file()}
    graph = build_graph(InMemorySaver())
    await graph.ainvoke(initial(env), cfg(env))
    snap = await graph.aget_state(cfg(env))
    assert snap.next == () and snap.values["report_id"]
    assert {"SECCONFIG_PERMIT_ALL", "TOOL_NO_APPROVAL"} <= {f["category"] for f in snap.values["correlated"]}
    assert not snap.values.get("fix_proposals")
    after = {str(p.relative_to(workspace)): p.read_bytes() for p in workspace.rglob("*") if p.is_file()}
    assert before == after
    async with session_scope() as db:
        assert not (await db.execute(select(Approval))).scalars().all()
        assert not (await db.execute(select(FixProposalRow))).scalars().all()
        rows = (await db.execute(select(Finding).where(Finding.audit_id == uuid.UUID(env["audit"])))).scalars().all()
        assert rows and all(f.status == "OPEN" for f in rows)
        report = (await db.execute(select(Report).where(Report.audit_id == uuid.UUID(env["audit"])))).scalar_one()
        assert "보안 감사 보고서" in report.report_md
        assert "## 수정·PR" not in report.report_md and "## 재검증" not in report.report_md


async def test_retired_mutation_tools_are_unavailable(env: dict[str, Any]) -> None:
    from core.errors import AppError
    from mcp_servers.business.app import mcp

    names = {tool.name for tool in await mcp.list_tools()}
    for tool in ["create_fix_proposal", "apply_fix", "merge_fix", "request_approval"]:
        assert tool not in names
        with pytest.raises(AppError):
            await inprocess_gateway().call(tool, {}, CallCtx("api", "ADMIN"))
