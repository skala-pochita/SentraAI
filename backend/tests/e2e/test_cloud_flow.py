"""Agent·Cloud 실환경 e2e: POST /api/ai/tasks → Kafka → audit-worker → 승인 대기 → 승인 → 티켓 1건 → 완료.

전제: `make up` (+ `make seed`). LLM 키가 없으면 결정론 모드(llm_used=false)로 동작한다.
실행: cd backend && uv run pytest -m infra tests/e2e/test_cloud_flow.py
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.infra

ROOT = Path(__file__).resolve().parents[3]
BASE = os.environ.get("API_URL_HOST", "http://localhost:8000")
ADMIN = ("admin@demo.io", os.environ.get("SEED_ADMIN_PASSWORD", "Admin!demo1234"))
DEV = ("dev@demo.io", os.environ.get("SEED_DEV_PASSWORD", "Dev!demo1234"))


def bundle(name: str = "T06") -> dict[str, Any]:
    d = json.loads((ROOT / "datasets" / "cloud" / "inputs" / f"{name}.json").read_text(encoding="utf-8"))
    d["business_policy"] = {**d["business_policy"], "followup_allowed": True, "ticket_target": "platform-team"}
    return d  # type: ignore[no-any-return]


async def login(c: httpx.AsyncClient, cred: tuple[str, str]) -> dict[str, str]:
    r = await c.post("/api/v1/auth/login", json={"email": cred[0], "password": cred[1]})
    assert r.status_code == 200, r.text
    return {"authorization": f"Bearer {r.json()['access_token']}"}


async def wait(
    c: httpx.AsyncClient, hdr: dict[str, str], tid: str, want: set[str], timeout: float = 120
) -> dict[str, Any]:
    end = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < end:
        last = (await c.get(f"/api/ai/tasks/{tid}", headers=hdr)).json()
        if last["status"] in want:
            return last
        assert last["status"] != "FAILED", last
        await asyncio.sleep(1.5)
    raise AssertionError(f"timeout waiting for {want}: {last}")


@pytest.fixture
async def c() -> Any:
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as client:
        yield client


async def test_task_to_ticket_with_worker_restart(c: httpx.AsyncClient) -> None:
    dev, admin = await login(c, DEV), await login(c, ADMIN)
    res = await c.post("/api/ai/tasks", json={"evidence_bundle": bundle()}, headers=dev)
    assert res.status_code == 202, res.text
    tid = res.json()["taskId"]

    snap = await wait(c, dev, tid, {"WAITING_APPROVAL"})
    pa = snap["answer"]["pendingAction"]
    assert pa["target"] == "platform-team" and pa["tool"] == "business_create_remediation_ticket"

    # 직접 호출해도 승인 없이는 티켓이 만들어지지 않는다 (요청자는 승인 불가)
    body = {"actionId": pa["actionId"], "decision": "APPROVE", "argsHash": pa["argsHash"]}
    assert (await c.post(f"/api/ai/tasks/{tid}/approval", json=body, headers=dev)).status_code == 403
    assert (
        await c.post(f"/api/ai/tasks/{tid}/approval", json={**body, "argsHash": "0" * 64}, headers=admin)
    ).status_code == 409

    # 승인 대기 중 audit-worker 재시작 → 같은 thread_id 로 재개
    subprocess.run(["docker", "compose", "restart", "audit-worker"], cwd=ROOT, check=True, capture_output=True)
    await asyncio.sleep(12)
    ok = await c.post(f"/api/ai/tasks/{tid}/approval", json=body, headers=admin)
    assert ok.status_code == 202, ok.text
    dup = await c.post(f"/api/ai/tasks/{tid}/approval", json=body, headers=admin)  # 재전송
    assert dup.status_code == 202

    done = await wait(c, dev, tid, {"COMPLETED"})
    assert done["answer"]["actionResult"]["status"] == "CREATED"
    assert done["sources"] and done["answer"]["findings"]
    tickets = subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "auditor",
            "-d",
            "auditor",
            "-tAc",
            f"select count(*) from remediation_tickets where task_id='{tid}'",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert tickets == "1"

    types: list[str] = []
    async with c.stream("GET", f"/api/ai/tasks/{tid}/stream", headers=dev, timeout=30) as r:
        async for line in r.aiter_lines():
            if line.startswith("event:"):
                types.append(line.split(":", 1)[1].strip())
    assert types[-1] == "DONE" and "TOOL_START" in types and "APPROVAL_REQUIRED" in types
    mine = (await c.get("/api/ai/tasks?mine=true", headers=dev)).json()
    assert tid in {t["taskId"] for t in mine}
