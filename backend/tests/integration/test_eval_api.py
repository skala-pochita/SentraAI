from __future__ import annotations

import json

import httpx

from api.seed import seed_users
from core.db.session import session_scope
from core.settings import get_settings


async def test_cloud_runs_listing_and_path_safety(client: httpx.AsyncClient, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("REPORTS_DIR", str(tmp_path))
    get_settings.cache_clear()
    ev = {
        "eval_id": "20261019T000000",
        "configs": ["A"],
        "bundles": ["T01"],
        "repetitions": 1,
        "models": [],
        "summary": {"A": {"executed": 0, "skipped": 1, "metrics": None}},
    }
    (tmp_path / "cloud_eval_20261019T000000.json").write_text(json.dumps(ev))
    async with session_scope() as db:
        await seed_users(db)
    s = get_settings()
    tok = (
        await client.post("/api/v1/auth/login", json={"email": "dev@demo.io", "password": s.seed_dev_password})
    ).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert (await client.get("/api/v1/eval/cloud-runs")).status_code == 401
    runs = (await client.get("/api/v1/eval/cloud-runs", headers=h)).json()
    assert runs[0]["skipped"] == {"A": 1}
    assert (await client.get("/api/v1/eval/cloud-runs/20261019T000000", headers=h)).json()["summary"]["A"][
        "metrics"
    ] is None
    assert (await client.get("/api/v1/eval/cloud-runs/..%2F..%2Fetc", headers=h)).status_code in (404, 422)
    get_settings.cache_clear()
