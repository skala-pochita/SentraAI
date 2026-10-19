"""FastAPI 엔트리포인트 (prefix /api/v1)."""

from __future__ import annotations

import logging

from fastapi import FastAPI

from api.errors import install_error_handlers
from api.routers import auth, ops, targets

API_PREFIX = "/api/v1"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = FastAPI(title="AI Security Auditor API", version="0.1.0")
install_error_handlers(app)

for r in (auth.router, targets.router, ops.router):
    app.include_router(r, prefix=API_PREFIX)
