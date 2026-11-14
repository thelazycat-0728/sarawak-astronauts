"""Error types and handlers that produce the API.md error shape.

Every error body is ``{"error": {"code": ..., "message": ...}}``.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("app.errors")

CODE_BY_STATUS = {
    400: "invalid_parameter",
    404: "not_found",
    429: "rate_limited",
    500: "internal_error",
    502: "upstream_error",
    503: "unavailable",
    504: "upstream_timeout",
}
_PARAM_LOCATIONS = {"query", "path", "body", "header", "cookie"}


class ApiError(Exception):
    """Raise from routes or providers to return a specific contract error."""

    status_code = 500
    code = "internal_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class InvalidParameter(ApiError):
    status_code = 400
    code = "invalid_parameter"


class NotFound(ApiError):
    status_code = 404
    code = "not_found"


class UpstreamError(ApiError):
    status_code = 502
    code = "upstream_error"


class UpstreamTimeout(ApiError):
    status_code = 504
    code = "upstream_timeout"


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"error": {"code": code, "message": message}}
    )


def format_validation_errors(exc: RequestValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = [str(p) for p in err.get("loc", ()) if p not in _PARAM_LOCATIONS]
        msg = str(err.get("msg", "invalid value")).removeprefix("Value error, ")
        parts.append(f"{'.'.join(loc)}: {msg}" if loc else msg)
    return "; ".join(parts) or "invalid request"


async def _validation_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    return error_response(400, "invalid_parameter", format_validation_errors(exc))


async def _http_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = CODE_BY_STATUS.get(exc.status_code, "http_error")
    message = exc.detail if isinstance(exc.detail, str) else code
    response = error_response(exc.status_code, code, message)
    if exc.headers:
        response.headers.update(exc.headers)
    return response


async def _api_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return error_response(exc.status_code, exc.code, exc.message)


async def catch_unhandled(request: Request, call_next):
    """HTTP middleware: turn any unhandled exception into a 500 with no traceback.

    Runs inside CORSMiddleware, so browsers can still read the error body.
    """
    try:
        return await call_next(request)
    except Exception:
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return error_response(500, "internal_error", "Internal server error")


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(StarletteHTTPException, _http_handler)
    app.add_exception_handler(ApiError, _api_error_handler)
