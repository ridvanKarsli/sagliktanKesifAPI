"""Alan hatalarını tek biçimli HTTP yanıtlarına eşler. Beklenmeyen hatalar ayrıntı sızdırmaz."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ...core.logging import request_id_var
from ...domain.errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    DomainError,
    ExternalServiceError,
    NotFoundError,
    ValidationError,
)

log = logging.getLogger(__name__)

STATUS = {
    NotFoundError: 404,
    ConflictError: 409,
    ValidationError: 422,
    AuthenticationError: 401,
    AuthorizationError: 403,
    ExternalServiceError: 502,
}


def _body(code: str, message: str, details=None, status: int = 500) -> JSONResponse:
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    return JSONResponse({"error": {"code": code, "message": message, "details": details or {}, "request_id": request_id_var.get()}},
                        status_code=status, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(request: Request, exc: DomainError):
        status = next((s for t, s in STATUS.items() if isinstance(exc, t)), 400)
        return _body(exc.code, exc.message, exc.details, status)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        errors = [{"loc": [str(x) for x in e.get("loc", [])], "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return _body("validation_error", "İstek doğrulanamadı.", {"errors": errors}, 422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        return _body("http_error", str(exc.detail), None, exc.status_code)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        log.exception("Beklenmeyen hata: %s %s", request.method, request.url.path)
        return _body("internal_error", "Beklenmeyen bir hata oluştu.", None, 500)
