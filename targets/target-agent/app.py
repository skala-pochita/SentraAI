"""샘플 사내 CS 에이전트 (감사 대상). Phase 2에서 구현."""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(title="target-agent (cs-helper)")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
