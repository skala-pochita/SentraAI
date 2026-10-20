"""intake: 대상 로드, Redis 락 획득(실패 시 CONFLICT), 위험 파일 휴리스틱으로 plan 작성."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig

from core.errors import AppError, ErrorCode
from core.graph.deps import get_deps
from core.graph.nodes._base import node
from core.redis.locks import acquire_target_lock
from harness.budget import limits


def risky_files(files: list[dict[str, Any]], keywords: list[str], top_k: int) -> list[str]:
    def score(path: str) -> int:
        name = path.rsplit("/", 1)[-1]
        return sum(2 if k.lower() in name.lower() else 0 for k in keywords) + (1 if name.endswith(".java") else 0)

    ranked = sorted((f["path"] for f in files if score(f["path"]) >= 2), key=lambda p: (-score(p), p))
    return ranked[:top_k]


@node("intake")
async def intake(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
    deps = get_deps(config)
    wanted = {t["target_id"] for t in state.get("targets", [])}
    all_targets = await deps.call(state, "intake", "list_audit_targets")
    targets = [t for t in all_targets if t["target_id"] in wanted]
    if len(targets) != len(wanted):
        raise AppError(
            ErrorCode.NOT_FOUND,
            "some audit targets not found",
            {"missing": sorted(wanted - {t["target_id"] for t in targets})},
        )
    for t in targets:
        await acquire_target_lock(t["target_id"], state["audit_id"])

    react = limits().get("react", {})
    plan: dict[str, Any] = {"repos": {}, "agents": {}, "mode": state.get("mode", "full")}
    for t in targets:
        if t["type"] == "repo":
            files = await deps.call(state, "intake", "list_repo_files", target_id=t["target_id"])
            plan["repos"][t["target_id"]] = {
                "file_count": len(files),
                "files": [f["path"] for f in files],
                "sizes": {f["path"]: f["size"] for f in files},
                "risky_files": risky_files(
                    files, react.get("risky_name_keywords", []), int(react.get("risky_file_top_k", 8))
                ),
                "commit_sha": t.get("commit_sha"),
            }
        else:
            m = await deps.call(state, "intake", "get_agent_manifest", target_id=t["target_id"])
            plan["agents"][t["target_id"]] = {
                "tools": [x["name"] for x in m["manifest"].get("tools", [])],
                "backend_api": m["manifest"].get("backend_api"),
                "runtime_status": m.get("runtime_status", "CONFIGURED"),
                "discovery": m.get("discovery", "explicit_manifest"),
            }
    await deps.event(
        state,
        "plan.ready",
        {
            "targets": [t["name"] for t in targets],
            "risky_files": {k: v["risky_files"] for k, v in plan["repos"].items()},
        },
        node="intake",
    )
    return {"targets": targets, "plan": plan, "probe_round": 0, "retry_count": 0}
