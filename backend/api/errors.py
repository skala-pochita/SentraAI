"""API 오류 응답 {"error": {code, message, details}} 와 페이지네이션."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.errors import HTTP_STATUS, AppError, ErrorCode

log = logging.getLogger("api")


def _body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or {}}}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.code == ErrorCode.UNAUTHORIZED else None
        return JSONResponse(
            _body(str(exc.code), exc.message, exc.details), status_code=HTTP_STATUS[exc.code], headers=headers
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(_body("VALIDATION", "request validation failed", {"errors": [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in exc.errors()]}), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "NOT_FOUND", 405: "VALIDATION", 401: "UNAUTHORIZED", 403: "FORBIDDEN"}.get(
            exc.status_code, "INTERNAL"
        )
        return JSONResponse(_body(code, str(exc.detail)), status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error: %s", type(exc).__name__)
        return JSONResponse(_body("INTERNAL", "internal error"), status_code=500)


class Page(BaseModel):
    page: int
    size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size


def page_params(page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=200)) -> Page:
    return Page(page=page, size=size)


def paged(items: list[Any], total: int, p: Page) -> dict[str, Any]:
    return {"items": items, "total": total, "page": p.page, "size": p.size}
