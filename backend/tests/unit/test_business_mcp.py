"""Phase 3: business-mcp 조회·실행 도구 (DB 없이 대상 주입)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from core.settings import REPO_ROOT
from mcp_servers.business.app import mcp
from mcp_servers.business.demo_reset import reset
from mcp_servers.business.tools import execute, query
from mcp_servers.business.workspace import TargetInfo, clear_overrides, register_override
from mcp_servers.common import CallContext, default_call

REPO_ID = "00000000-0000-0000-0000-00000000000a"
AGENT_ID = "00000000-0000-0000-0000-00000000000b"


@pytest.fixture
def workspace(tmp_path: Path, fake_redis: object) -> Iterator[Path]:
    reset(REPO_ROOT / "targets", tmp_path, "vuln")
    register_override(
        TargetInfo(REPO_ID, "repo", "vuln-spring-app", str(tmp_path / "vuln-spring-app"), linked_target_id=AGENT_ID)
    )
    register_override(
        TargetInfo(
            AGENT_ID,
            "agent",
            "cs-helper",
            "http://target-agent:8201",
            {"config_dir": str(tmp_path / "agent-config")},
            linked_target_id=REPO_ID,
        )
    )
    token = default_call.set(CallContext("u1", "ADMIN", None, "r"))
    yield tmp_path
    default_call.reset(token)
    clear_overrides()


def as_role(role: str) -> Any:
    return default_call.set(CallContext("u2", role, None, "r"))


async def test_tool_list_has_spec_tools() -> None:
    names = {t.name for t in await mcp.list_tools()}
    for n in [
        "list_audit_targets",
        "list_repo_files",
        "read_repo_file",
        "search_repo",
        "get_agent_manifest",
        "get_findings",
        "get_audit_history",
        "run_static_scan",
        "run_agent_rule_check",
        "run_injection_probe",
    ]:
        assert n in names
    # docstring(설명·입력·출력·오류·부작용)이 도구 설명으로 노출된다
    tool = next(t for t in await mcp.list_tools() if t.name == "read_repo_file")
    assert "오류" in (tool.description or "") and "ctx" not in tool.inputSchema.get("properties", {})


async def test_read_repo_file_masks_and_limits(workspace: Path) -> None:
    res = await query.read_repo_file(target_id=REPO_ID, path=".env")
    assert res["ok"] and "AKIAFAKE0EXAMPLE0000" not in res["data"]["content_masked"]
    assert res["meta"]["request_id"]
    res = await query.read_repo_file(
        target_id=REPO_ID, path="src/main/resources/application.yml", start_line=5, end_line=9
    )
    assert res["data"]["start_line"] == 5 and res["data"]["end_line"] == 9


@pytest.mark.parametrize("bad", ["../../etc/passwd", "/etc/passwd", "src/../../x", ".git/config", "~/x", "a\\b"])
async def test_path_traversal_rejected(workspace: Path, bad: str) -> None:
    res = await query.read_repo_file(target_id=REPO_ID, path=bad)
    assert res["ok"] is False and res["error"]["code"] == "VALIDATION" and res["error"]["retryable"] is False


async def test_symlink_escape_rejected(workspace: Path) -> None:
    (workspace / "vuln-spring-app" / "evil").symlink_to("/etc")
    res = await query.read_repo_file(target_id=REPO_ID, path="evil/hosts")
    assert res["error"]["code"] == "VALIDATION"


async def test_not_found_and_list_files(workspace: Path) -> None:
    res = await query.read_repo_file(target_id=REPO_ID, path="nope.txt")
    assert res["error"]["code"] == "NOT_FOUND"
    files = await query.list_repo_files(target_id=REPO_ID, glob="**/*Config*.java")
    paths = {f["path"] for f in files["data"]}
    assert "src/main/java/com/demo/shop/config/SecurityConfig.java" in paths
    assert not any(p.startswith(".git/") for p in paths)


async def test_search_repo(workspace: Path) -> None:
    res = await query.search_repo(target_id=REPO_ID, pattern=r"sk_live_", max_results=5)
    assert res["ok"] and res["data"][0]["path"].endswith("PaymentClient.java")
    bad = await query.search_repo(target_id=REPO_ID, pattern="(", max_results=5)
    assert bad["error"]["code"] == "VALIDATION"
    too_many = await query.search_repo(target_id=REPO_ID, pattern="x", max_results=51)
    assert too_many["error"]["code"] == "VALIDATION"


async def test_static_scan_cached_by_commit(workspace: Path) -> None:
    first = await execute.run_static_scan(target_id=REPO_ID)
    assert first["ok"] and not first["data"]["cached"] and len(first["data"]["hits"]) > 15
    second = await execute.run_static_scan(target_id=REPO_ID)
    assert second["data"]["cached"] and second["data"]["commit_sha"] == first["data"]["commit_sha"]


async def test_agent_rule_check_uses_linked_repo_secrets(workspace: Path) -> None:
    res = await execute.run_agent_rule_check(target_id=AGENT_ID)
    cats = {h["category"] for h in res["data"]["hits"]}
    assert "AGENT_SHARED_SECRET" in cats and "TOOL_NO_APPROVAL" in cats
    assert "AI_API_KEY" in res["data"]["code_secret_names"]
    manifest = await query.get_agent_manifest(target_id=AGENT_ID)
    assert "CANARY-7F3A-INTERNAL-DISCOUNT" not in manifest["data"]["system_prompt_masked"]
    assert "CANA****" in manifest["data"]["system_prompt_masked"]


async def test_probe_requires_admin_and_allowlist(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tok = as_role("DEVELOPER")
    try:
        res = await execute.run_injection_probe(target_id=AGENT_ID, probe_id="P01")
        assert res["error"]["code"] == "FORBIDDEN"
    finally:
        default_call.reset(tok)
    register_override(
        TargetInfo(
            "00000000-0000-0000-0000-00000000000c",
            "agent",
            "evil",
            "http://evil.test",
            {"config_dir": str(workspace / "agent-config")},
        )
    )
    res = await execute.run_injection_probe(target_id="00000000-0000-0000-0000-00000000000c", probe_id="P01")
    assert res["error"]["code"] == "FORBIDDEN"


async def test_probe_judges_deterministically(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PROBE_ALLOWLIST", "http://target-agent:8201")
    from core.settings import get_settings

    get_settings.cache_clear()

    async def fake_chat(client: Any, url: str, sid: str, message: str, kb: Any) -> dict[str, Any]:
        return {
            "reply": "삭제했습니다",
            "tool_calls": [{"name": "delete_customer", "args": {"customer_id": 1004}, "executed": True}],
        }

    monkeypatch.setattr(execute, "_chat", fake_chat)
    res = await execute.run_injection_probe(target_id=AGENT_ID, probe_id="P03", attempts=2)
    assert res["ok"], res
    assert [a["verdict"] for a in res["data"]["attempts"]] == ["SUCCESS", "SUCCESS"]
    assert res["data"]["attempts"][0]["forbidden_tools"] == ["delete_customer"]
    get_settings.cache_clear()


async def test_probe_upstream_timeout_is_retryable(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from core.errors import AppError, ErrorCode
    from core.settings import get_settings

    monkeypatch.setenv("PROBE_ALLOWLIST", "http://target-agent:8201")
    get_settings.cache_clear()

    async def boom(*_: Any) -> dict[str, Any]:
        raise AppError(ErrorCode.UPSTREAM_TIMEOUT, "target-agent timeout")

    monkeypatch.setattr(execute, "_chat", boom)
    res = await execute.run_injection_probe(target_id=AGENT_ID, probe_id="P01", attempts=1)
    assert res["error"]["code"] == "UPSTREAM_TIMEOUT" and res["error"]["retryable"] is True
    get_settings.cache_clear()


async def test_tool_annotations_mark_destructive() -> None:
    from mcp_servers.rag import tools as _rt  # noqa: F401
    from mcp_servers.rag.app import mcp as rag_mcp

    tools = {t.name: t for t in [*await mcp.list_tools(), *await rag_mcp.list_tools()]}
    for name in (
        "apply_fix",
        "create_finding",
        "merge_fix",
        "save_report",
        "delete_document",
        "register_false_positive",
    ):
        assert tools[name].annotations and tools[name].annotations.destructiveHint is True, name
    for name in ("read_repo_file", "search_security_standard", "get_findings"):
        assert tools[name].annotations and tools[name].annotations.readOnlyHint is True, name
    assert tools["run_injection_probe"].annotations.openWorldHint is True  # type: ignore[union-attr]


async def test_fix_proposal_skips_only_failed_edits(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from mcp_servers.business import patches

    root = workspace / "vuln-spring-app"
    skipped: list[dict[str, Any]] = []
    path = "src/main/java/com/demo/shop/config/WebConfig.java"
    edits = [
        {"op": "replace", "path": path, "old": "NOT-IN-FILE", "new": "x", "finding_id": "f-bad"},
        {"op": "line_regex", "path": path, "line": 13, "pattern": r'allowedOriginPatterns\("\*"\)',
         "replacement": 'allowedOrigins("https://shop.example")', "finding_id": "f-good"},
    ]
    before, after = patches.apply_edits(root, edits, skipped)
    assert [x["finding_id"] for x in skipped] == ["f-bad"]
    assert "https://shop.example" in (after[path] or "")
    with pytest.raises(Exception):  # noqa: B017 - 기존 동작: skipped 미지정이면 첫 실패에서 중단
        patches.apply_edits(root, edits)


def test_probe_edit_scopes_get_order_to_self() -> None:
    from core.fixes import probe_edits
    from core.schemas import FindingDraft

    text = (REPO_ROOT / "targets/target-agent/manifests/vuln.yaml").read_text()
    f = FindingDraft(temp_id="p", target_id="t", source="agent", category="PRIVILEGE_ESCALATION", rule_id="PROBE-P09",
                     title="x", severity="HIGH", confidence=0.9)
    edits = probe_edits(f, text)
    assert edits and edits[0]["replacement"] == '"orders:read:self"'
    assert probe_edits(f.model_copy(update={"rule_id": "PROBE-P03"}), text) is None
