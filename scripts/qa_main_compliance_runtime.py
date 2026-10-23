"""QA only: real PostgreSQL/original corpus/API/worker; simulated target and Redis.

Run after migration on a dedicated empty QA database. OPENAI_API_KEY is forced
empty. This does not start Docker sandboxes or send Kafka/LLM requests.
Optional --serve-port exposes the real local-mode API on 127.0.0.1 for UI QA.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--serve-port", type=int)
    args = parser.parse_args()
    if "sentra_qa_" not in args.database_url.rsplit("/", 1)[-1]:
        parser.error("Use a dedicated sentra_qa_* database")
    os.environ.update(
        DATABASE_URL=args.database_url,
        OPENAI_API_KEY="",
        AUTH_MODE="local",
        BCRYPT_COST="4",
        PROBE_ATTEMPTS="1",
        PROBE_ALLOWLIST="http://target-agent:8201",
        KNOWLEDGE_DIR=str(ROOT / "knowledge"),
        CONFIG_DIR=str(ROOT / "config"),
        COMPLIANCE_CONFIG_DIR=str(ROOT / "config/compliance"),
    )
    os.environ.pop("REGULATION_INDEX_PATH", None)
    import api.routers.audits as audits
    import fakeredis
    import httpx
    import workers.audit as worker_module
    import yaml
    from api.local_mode import local_user
    from api.main import app
    from core.compliance import load_catalog, verify_report
    from core.db.models import Audit, Report, Target
    from core.db.session import get_engine, session_scope
    from core.graph.deps import Deps
    from core.mcp_client import inprocess_gateway
    from core.redis.client import set_redis
    from core.settings import get_settings
    from langgraph.checkpoint.memory import InMemorySaver
    from mcp_servers.business.demo_reset import reset
    from mcp_servers.business.tools import execute
    from mcp_servers.rag.regulations import exact_clause, ingest_regulations, search_clauses
    from sqlalchemy import func, select
    from tests.fakes import GullibleAgent
    from workers.audit import AuditRunner

    get_settings.cache_clear()
    set_redis(fakeredis.FakeAsyncRedis(decode_responses=True))
    output = ROOT / "reports/main-compliance-qa"
    output.mkdir(parents=True, exist_ok=True)
    async with session_scope() as db:
        if (await db.execute(select(func.count()).select_from(Audit))).scalar_one():
            raise RuntimeError("QA database must contain no audits; use a new database")
        user = await local_user(db)
        uid = user.id
    index = await ingest_regulations()
    queries = []
    for case in yaml.safe_load((ROOT / "datasets/compliance/retrieval_acceptance.yaml").read_text())["cases"]:
        hits = await search_clauses(case["query"], case.get("source"), 5)
        for hit in hits:
            assert (await exact_clause(hit["clause_id"]))["text"] == hit["text"]
        passed = not hits if case.get("expect_empty") else any(
            h["clause_id"].startswith(prefix) for h in hits for prefix in case["expected_prefixes"]
        )
        queries.append({"id": case["id"], "passed": passed, "hits": [h["clause_id"] for h in hits]})
    (output / "postgres-corpus-search.json").write_text(json.dumps({"index": index, "queries": queries}, ensure_ascii=False, indent=2))
    assert all(q["passed"] for q in queries), "Real PostgreSQL corpus retrieval acceptance failed"
    catalog = load_catalog()
    assert not catalog.missing_sources and catalog.links
    runner = AuditRunner(InMemorySaver(), gateway_factory=inprocess_gateway, deps_factory=lambda gw: Deps(gateway=gw))
    completed = []
    project_id = str(uuid.uuid4())
    baseline = None
    previous_repo = None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
        for version, variant in enumerate(["vuln", "hardened"], 1):
            workspace = ROOT / "workspace" / f"main-compliance-qa-v{version}"
            reset(ROOT / "targets", workspace, variant)
            simulator = GullibleAgent(workspace / "agent-config")

            async def chat(client: Any, url: str, sid: str, message: str, kb: Any, simulated: Any = simulator) -> dict[str, Any]:
                return simulated.chat(message, kb)

            async with session_scope() as db:
                repo = Target(type="repo", name=f"QA-v{version}-source", ref=str(workspace / "vuln-spring-app"), owner_user_id=uid,
                              config={"project_id": project_id, "version": version, "previous_target_id": previous_repo})
                db.add(repo)
                await db.flush()
                agent = Target(type="agent", name=f"QA-v{version}-agent-MOCK", ref="http://target-agent:8201", linked_target_id=repo.id,
                               config={"config_dir": str(workspace / "agent-config")}, owner_user_id=uid)
                db.add(agent)
                await db.flush()
                repo.linked_target_id = agent.id
                tids = [str(repo.id), str(agent.id)]
                previous_repo = str(repo.id)
            body = {"target_ids": tids, "mode": "full", "compliance_profile": {"sector": "finance", "high_impact_ai": "UNDETERMINED"}}
            if baseline:
                body["baseline_audit_id"] = baseline
            with patch.object(audits, "publish", AsyncMock()), patch.object(worker_module, "publish", AsyncMock()), patch.object(execute, "_chat", chat):
                response = await client.post("/api/v1/audits", json=body, headers={"X-Sentra-Local": "1"})
                assert response.status_code == 202, response.text
                aid = response.json()["audit_id"]
                assert await runner.run(aid) == "COMPLETED"
            async with session_scope() as db:
                saved = (await db.execute(select(Report).where(Report.audit_id == uuid.UUID(aid)))).scalar_one()
                report, markdown = saved.report_json, saved.report_md
            assert verify_report(report) and report["compliance"]["recommendations"]
            assert not any(c.get("model_verification") == "LIVE_VERIFIED" for c in report["agent_coverage"])
            export = await client.get(f"/api/v1/audits/{aid}/compliance")
            assert export.status_code == 200 and export.json()["report_snapshot"] == report
            assert (await client.get(f"/api/v1/audits/{aid}/report.md")).text == markdown
            (output / f"runtime-v{version}-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
            (output / f"runtime-v{version}-report.md").write_text(markdown)
            completed.append({"audit_id": aid, "version": version, "findings": len(report["findings"]), "risk_paths": len(report["risk_paths"]),
                              "recommendations": len(report["compliance"]["recommendations"]), "status": "COMPLETED"})
            if version == 1:
                assert report["risk_paths"] and any(f["compliance_refs"] for f in report["findings"])
                baseline = aid
            else:
                comparison = await client.get(f"/api/v1/audits/{aid}/compare")
                assert comparison.status_code == 200, comparison.text
                assert comparison.json()["baseline"]["version"] == 1 and comparison.json()["current"]["version"] == 2
                (output / "runtime-comparison.json").write_text(json.dumps(comparison.json(), ensure_ascii=False, indent=2))
    result = {"database": "dedicated QA PostgreSQL", "originals": 5, "index": index, "queries_passed": sum(q["passed"] for q in queries),
              "target": "simulated external agent (not a real LLM)", "redis": "fakeredis", "llm_calls": 0, "kafka": "publish simulated", "audits": completed}
    (output / "runtime-summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    if args.serve_port:
        import uvicorn

        app.state.background_consumers = False
        await uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=args.serve_port, log_level="warning")).serve()
    await get_engine().dispose()


if __name__ == "__main__":
    asyncio.run(main())
