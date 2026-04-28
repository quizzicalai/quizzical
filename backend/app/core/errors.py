"""§18 — Unified API error envelope and domain exception hierarchy.

Provides:
  * `AppError` and a tiny set of subclasses for common failure modes.
  * `build_error_envelope()` — the canonical serialiser used by every
    error-producing path so the FE always sees the same JSON shape.
  * `install_error_handlers(app)` — registers handlers for `AppError`,
    `HTTPException`, `RequestValidationError`, and the catch-all
    `Exception` so unexpected failures still surface a clean envelope.

Envelope shape (AC-QUALITY-ERR-1..4):

    {
        "detail":    "<human readable summary>",
        "errorCode": "<stable machine code, SCREAMING_SNAKE>",
        "traceId":   "<X-Trace-ID echoed>",
        "details":   <optional structured context, omitted when None>
    }
"""

from __future__ import annotations

from typing import Any, Final

import structlog
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Domain exception hierarchy
# ---------------------------------------------------------------------------


class AppError(Exception):
    """Base class for all domain errors raised by the backend.

    Subclasses set ``http_status`` and ``error_code`` class attributes so the
    global handler can render a uniform envelope without per-endpoint glue.
    """

    http_status: int = 500
    error_code: str = "INTERNAL_SERVER_ERROR"

    def __init__(self, message: str, *, details: Any | None = None) -> None:
        super().__init__(message)
        self.details = details


class BadRequestError(AppError):
    http_status = 400
    error_code = "BAD_REQUEST"


class UnauthorizedError(AppError):
    http_status = 401
    error_code = "UNAUTHORIZED"


class ForbiddenError(AppError):
    http_status = 403
    error_code = "FORBIDDEN"


class NotFoundError(AppError):
    http_status = 404
    error_code = "NOT_FOUND"


class ConflictError(AppError):
    http_status = 409
    error_code = "CONFLICT"


class SessionBusyError(ConflictError):
    error_code = "SESSION_BUSY"


class PayloadTooLargeError(AppError):
    http_status = 413
    error_code = "PAYLOAD_TOO_LARGE"


class ValidationFailedError(AppError):
    http_status = 422
    error_code = "VALIDATION_ERROR"


class RateLimitedError(AppError):
    http_status = 429
    error_code = "RATE_LIMITED"


class ServiceUnavailableError(AppError):
    http_status = 503
    error_code = "SERVICE_UNAVAILABLE"


# ---------------------------------------------------------------------------
# Envelope construction
# ---------------------------------------------------------------------------


_STATUS_TO_CODE: Final[dict[int, str]] = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_SERVER_ERROR",
    503: "SERVICE_UNAVAILABLE",
}


def default_error_code_for_status(status_code: int) -> str:
    """Map an HTTP status code to its canonical ``errorCode`` string."""
    if status_code in _STATUS_TO_CODE:
        return _STATUS_TO_CODE[status_code]
    if 400 <= status_code < 500:
        return "BAD_REQUEST"
    return "INTERNAL_SERVER_ERROR"


def _current_trace_id() -> str:
    """Read the structlog-bound trace id, falling back to a stable sentinel."""
    try:
        ctx = structlog.contextvars.get_contextvars()
        return str(ctx.get("trace_id") or "not_found")
    except Exception:
        return "not_found"


def build_error_envelope(
    *,
    status_code: int,
    detail: str,
    error_code: str | None = None,
    trace_id: str | None = None,
    details: Any | None = None,
) -> dict[str, Any]:
    """Construct the canonical error envelope dict."""
    body: dict[str, Any] = {
        "detail": detail,
        "errorCode": error_code or default_error_code_for_status(status_code),
        "traceId": trace_id or _current_trace_id(),
    }
    if details is not None:
        body["details"] = details
    return body


# ---------------------------------------------------------------------------
# Global handlers
# ---------------------------------------------------------------------------


_GENERIC_5XX_DETAIL: Final[str] = (
    "An unexpected internal error occurred. Our wizards have been notified."
)


async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    body = build_error_envelope(
        status_code=exc.http_status,
        detail=str(exc) or default_error_code_for_status(exc.http_status),
        error_code=exc.error_code,
        details=exc.details,
    )
    return JSONResponse(status_code=exc.http_status, content=body)


async def _http_exception_handler(
    request: Request, exc: HTTPException
) -> JSONResponse:
    """Wrap classic ``HTTPException`` into the unified envelope.

    Handlers may raise ``HTTPException(detail={...})`` to opt into a custom
    ``errorCode``; we honour that without overwriting their explicit code.
    """
    detail = exc.detail
    error_code: str | None = None
    extra_details: Any | None = None
    detail_text: str

    if isinstance(detail, dict):
        # Pre-built envelope-ish dict: pull through known keys.
        detail_text = str(detail.get("detail") or detail.get("message") or "")
        if not detail_text:
            detail_text = default_error_code_for_status(exc.status_code)
        error_code = detail.get("errorCode") or detail.get("error_code")
        # Preserve any extra context the handler attached (rate-limit headers, etc.).
        leftover = {
            k: v
            for k, v in detail.items()
            if k not in {"detail", "message", "errorCode", "error_code"}
        }
        extra_details = jsonable_encoder(leftover) if leftover else None
    elif isinstance(detail, list):
        detail_text = default_error_code_for_status(exc.status_code)
        extra_details = jsonable_encoder(detail)
    else:
        detail_text = str(detail) if detail else default_error_code_for_status(
            exc.status_code
        )

    body = build_error_envelope(
        status_code=exc.status_code,
        detail=detail_text,
        error_code=error_code,
        details=extra_details,
    )
    headers = getattr(exc, "headers", None)
    return JSONResponse(status_code=exc.status_code, content=body, headers=headers)


async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    body = build_error_envelope(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="Request validation failed.",
        error_code="VALIDATION_ERROR",
        details=jsonable_encoder(exc.errors()),
    )
    return JSONResponse(status_code=422, content=body)


async def _unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    logger.exception(
        "unhandled_exception",
        error=str(exc),
        error_type=type(exc).__name__,
        path=request.url.path,
    )
    body = build_error_envelope(
        status_code=500,
        detail=_GENERIC_5XX_DETAIL,
        error_code="INTERNAL_SERVER_ERROR",
    )
    return JSONResponse(status_code=500, content=body)


def install_error_handlers(app: FastAPI) -> None:
    """Register the unified envelope handlers on ``app``."""
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(HTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)


__all__ = [
    "AppError",
    "BadRequestError",
    "ConflictError",
    "ForbiddenError",
    "NotFoundError",
    "PayloadTooLargeError",
    "RateLimitedError",
    "ServiceUnavailableError",
    "SessionBusyError",
    "UnauthorizedError",
    "ValidationFailedError",
    "build_error_envelope",
    "default_error_code_for_status",
    "install_error_handlers",
]
