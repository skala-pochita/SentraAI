"""Phase 5/6 실환경 e2e: POST /audits → Kafka → audit-worker → read-only report.

전제: `make up && make demo-reset && make seed`. 실행: `cd backend && uv run pytest -m infra tests/e2e/test_demo_flow.py`
LLM 키 없이도 동작한다(규칙 엔진 + 결정론 수정기).
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.infra

BASE = os.environ.get("API_URL_HOST", "http://localhost:8000/api/v1")
ADMIN = ("admin@demo.io", os.environ.get("SEED_ADMIN_PASSWORD", "Admin!demo1234"))
TIMEOUT_S = 240


async def _wait(client: httpx.AsyncClient, audit_id: str, want: set[str]) -> dict[str, Any]:
    deadline = time.monotonic() + TIMEOUT_S
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = (await client.get(f"/audits/{audit_id}")).json()
        if last["status"] in want:
            return last
        assert last["status"] != "FAILED", last
        await asyncio.sleep(2)
    raise AssertionError(f"timeout waiting for {want}: {last}")


async def _cancel_active(client: httpx.AsyncClient) -> None:
    """이전 실행이 남긴 진행 중 감사는 대상 락을 잡고 있으므로 취소한다."""
    items = (await client.get("/audits", params={"size": 50})).json()["items"]
    for a in items:
        if a["status"] in ("QUEUED", "RUNNING", "WAITING_APPROVAL", "WAITING_MERGE"):
            await client.post(f"/audits/{a['id']}/cancel")


@pytest.fixture
async def client() -> Any:
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as c:
        res = await c.post("/auth/login", json={"email": ADMIN[0], "password": ADMIN[1]})
        assert res.status_code == 200, res.text
        c.headers["authorization"] = f"Bearer {res.json()['access_token']}"
        await _cancel_active(c)
        yield c
        await _cancel_active(c)


async def _start(client: httpx.AsyncClient) -> str:
    targets = (await client.get("/targets")).json()["items"]
    ids = [t["id"] for t in targets if t["type"] in ("repo", "agent")]
    assert len(ids) >= 2, targets
    res = await client.post("/audits", json={"target_ids": ids, "mode": "full"})
    assert res.status_code == 202, res.text
    return str(res.json()["audit_id"])


def _demo_reset() -> None:
    """이전 실행의 수정(병합)이 /workspace 에 남아 있으면 병합할 수정안이 없으므로 초기화한다."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    subprocess.run(["make", "demo-reset"], cwd=root, check=True, capture_output=True)


async def test_full_flow_read_only_report(client: httpx.AsyncClient) -> None:
    audit_id = await _start(client)
    await _wait(client, audit_id, {"COMPLETED"})
    report = await client.get(f"/audits/{audit_id}/report")
    assert report.status_code == 200, report.text
    assert "fix_proposals" not in report.json()


async def test_sse_receives_events(client: httpx.AsyncClient) -> None:
    audit_id = await _start(client)
    seen: list[str] = []
    async with client.stream("GET", f"/audits/{audit_id}/events", timeout=60) as r:
        assert r.status_code == 200
        async for line in r.aiter_lines():
            if line.startswith("event:"):
                seen.append(line.split(":", 1)[1].strip())
            if len(seen) >= 3:
                break
    assert len(seen) >= 3
    await client.post(f"/audits/{audit_id}/cancel")
