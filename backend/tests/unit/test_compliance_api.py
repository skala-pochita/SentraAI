"""HTTP routing/RBAC and immutable stored report exports without external infrastructure."""

from __future__ import annotations

import copy
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from api.deps import CurrentUser, get_current_user
from api.main import app
from api.routers import results
from core.compliance import seal_report, verify_report
from core.db.session import get_db


@pytest.mark.parametrize("role,expected", [("ADMIN", 200), ("AUDITOR", 200), ("DEVELOPER", 403)])
@pytest.mark.parametrize("suffix", ["compliance", "compliance.md"])
async def test_export_permissions_and_saved_snapshot(monkeypatch, role, expected, suffix):
    aid = uuid.uuid4()
    report = seal_report(
        {
            "audit_id": str(aid),
            "generated_at": "2026-10-23T00:00:00Z",
            "targets": [],
            "compliance": {
                "schema": 2,
                "status": "NOT_ASSESSED",
                "catalog_version": "old-pinned",
                "catalog_digest": "sha256:old",
                "recommendations": [],
                "missing_sources": [],
            },
        }
    )
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(report_json=report)))
    )
    check = AsyncMock()
    monkeypatch.setattr(results, "require_completed", check)

    async def user():
        return CurrentUser(uuid.uuid4(), "u@x.test", "u", role, "j", 0)

    async def session():
        yield db

    app.dependency_overrides[get_current_user] = user
    app.dependency_overrides[get_db] = session
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get(f"/api/v1/audits/{aid}/{suffix}")
        assert response.status_code == expected, response.text
        if expected == 200:
            check.assert_awaited_once()
            if suffix == "compliance":
                assert response.json()["catalog_version"] == "old-pinned"
                assert verify_report(response.json()["report_snapshot"])
            else:
                assert "검토 권고" in response.text and "old-pinned" in response.text
        else:
            check.assert_not_called()
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("tamper", ["alter", "remove_integrity", "malformed_integrity"])
async def test_tampered_saved_report_is_blocked(monkeypatch, tamper):
    report = seal_report({"compliance": {"schema": 2, "recommendations": []}})
    if tamper == "alter":
        report["altered"] = True
    elif tamper == "remove_integrity":
        report["compliance"].pop("integrity")
    else:
        report["compliance"]["integrity"] = "not a seal"
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(report_json=report)))
    )
    monkeypatch.setattr(results, "require_completed", AsyncMock())
    user = CurrentUser(uuid.uuid4(), "u@x.test", "u", "ADMIN", "j", 0)
    from core.errors import AppError

    with pytest.raises(AppError, match="integrity mismatch"):
        await results._compliance_snapshot(uuid.uuid4(), user, db)
    with pytest.raises(AppError, match="integrity mismatch"):
        await results.report_json(uuid.uuid4(), user, db)
    with pytest.raises(AppError, match="integrity mismatch"):
        await results.report_md(uuid.uuid4(), user, db)


async def test_old_report_without_section_is_rendered_without_remapping(monkeypatch):
    old = {"audit_id": "old", "generated_at": "old", "findings": []}
    db = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(report_json=copy.deepcopy(old)))
        )
    )
    monkeypatch.setattr(results, "require_completed", AsyncMock())
    user = CurrentUser(uuid.uuid4(), "u@x.test", "u", "ADMIN", "j", 0)
    response = await results.compliance_md(uuid.uuid4(), user, db)
    assert "저장되지 않았습니다" in response.body.decode()
