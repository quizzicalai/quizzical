"""§18 — Unified API error envelope and domain exception hierarchy.

Provides:
  * `AppError` and a tiny set of subclasses for common failure modes.
  * `build_error_envelope()` — the canonical serialiser used by every
    error-producing path so the FE always sees the same JSON shape.
  * `install_error_handlers(app)` — registers handlers for `AppError`,
    `HTTPException`, `RequestValidationError`, and the catch-all
    `Exception` so unexpected failures still surface a clean envelope.

Envelope shape (AC-QUALITY-ERR-1..4 + whimsical-error-system 2026-06-30):

    {
        "detail":    "<human readable summary>",
        "errorCode": "<stable machine code, SCREAMING_SNAKE — backward compat>",
        "code":      "<whimsical QF-... code, light-grey support-triage tag>",
        "whimsical": "<on-brand user-facing message that alludes to the cause>",
        "traceId":   "<X-Trace-ID echoed>",
        "details":   <optional structured context, omitted when None>
    }

The new ``code`` + ``whimsical`` fields (whimsical-error-system, owner request
2026-06-30) ride ALONGSIDE the existing ``errorCode``/``detail`` — they are
additive, so every existing consumer keeps working. ``code`` is the precise
internal ``QF-`` code (shown to the user as light-grey small text for support
triage); ``whimsical`` is the on-brand message the FE renders. The single
source of truth for both is :mod:`app.core.error_codes`.
"""

from __future__ import annotations

from typing import Any, Final

import structlog
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core import error_codes as ec

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Domain exception hierarchy
# ---------------------------------------------------------------------------


class AppError(Exception):
    """Base class for all domain errors raised by the backend.

    Subclasses set ``http_status`` and ``error_code`` class attributes so the
    global handler can render a uniform envelope without per-endpoint glue.

    ``qf_code`` (whimsical-error-system, 2026-06-30) optionally pins the precise
    :mod:`app.core.error_codes` ``QF-`` code for this error. Subclasses may set a
    ``qf_code`` class attribute, or callers may pass ``qf_code=`` per-raise. When
    omitted, the handler derives a sensible code from ``http_status``.
    """

    http_status: int = 500
    error_code: str = "INTERNAL_SERVER_ERROR"
    qf_code: str | None = None

    def __init__(
        self,
        message: str,
        *,
        details: Any | None = None,
        qf_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.details = details
        if qf_code is not None:
            self.qf_code = qf_code


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
    qf_code = ec.QF_QUIZ_NOT_FOUND


class ConflictError(AppError):
    http_status = 409
    error_code = "CONFLICT"
    qf_code = ec.QF_QUIZ_STALE_ANSWER


class SessionBusyError(ConflictError):
    error_code = "SESSION_BUSY"
    qf_code = ec.QF_SESSION_BUSY


class PayloadTooLargeError(AppError):
    http_status = 413
    error_code = "PAYLOAD_TOO_LARGE"


class ValidationFailedError(AppError):
    http_status = 422
    error_code = "VALIDATION_ERROR"


class RateLimitedError(AppError):
    http_status = 429
    error_code = "RATE_LIMITED"
    qf_code = ec.QF_RATE_LIMITED


class ServiceUnavailableError(AppError):
    http_status = 503
    error_code = "SERVICE_UNAVAILABLE"
    qf_code = ec.QF_SERVICE_UNAVAILABLE


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
    code: str | None = None,
    whimsical: str | None = None,
) -> dict[str, Any]:
    """Construct the canonical error envelope dict.

    ``code`` (the ``QF-`` whimsical code) and ``whimsical`` (the user-facing
    on-brand message) are ADDITIVE: they are included when present and omitted
    otherwise, so existing consumers that only read ``detail``/``errorCode``
    keep working unchanged.
    """
    body: dict[str, Any] = {
        "detail": detail,
        "errorCode": error_code or default_error_code_for_status(status_code),
        "traceId": trace_id or _current_trace_id(),
    }
    if code is not None:
        body["code"] = code
    if whimsical is not None:
        body["whimsical"] = whimsical
    if details is not None:
        body["details"] = details
    return body


def coded_http_exception(
    *,
    status_code: int,
    detail: str,
    code: str,
    headers: dict[str, str] | None = None,
) -> HTTPException:
    """Build an ``HTTPException`` whose ``detail`` is a plain human STRING and
    that carries the whimsical ``QF-`` ``code`` on a ``.qf_code`` attribute.

    The global ``_http_exception_handler`` reads ``.qf_code`` and emits ``code``
    + ``whimsical`` as SEPARATE top-level envelope fields, so ``detail`` stays a
    string for every consumer (FE + smoke tests). Prefer this helper over
    stuffing a dict into ``detail`` at every coded raise-site.
    """
    exc = HTTPException(status_code=status_code, detail=detail, headers=headers)
    # Attribute, not a detail dict — keeps the serialized ``detail`` a string.
    exc.qf_code = code  # type: ignore[attr-defined]
    return exc


def _resolve_spec(
    *, status_code: int, qf_code: str | None
) -> ec.ErrorCodeSpec:
    """Resolve the QF spec for a failure: explicit code wins, else by status."""
    if qf_code:
        return ec.get_spec(qf_code)
    return ec.spec_for_status(status_code)


def _maybe_notify_support_for(
    spec: ec.ErrorCodeSpec,
    request: Request | None,
    *,
    trace_id: str | None,
) -> None:
    """Fire the fire-and-forget Resend notify for a notify_support spec.

    Best-effort: importing/scheduling the notifier must never break error
    rendering, so the whole thing is guarded.
    """
    if not spec.notify_support:
        return
    try:
        from app.services.support_notify import maybe_notify_support

        path = None
        try:
            path = request.url.path if request is not None else None
        except Exception:
            path = None
        maybe_notify_support(
            spec,
            trace_id=trace_id or _current_trace_id(),
            path=path,
            context={"http_status": spec.http_status, "severity": spec.severity.value},
        )
    except Exception:
        logger.debug("error_envelope.notify_failed", code=spec.code, exc_info=True)


def _envelope_with_whimsy(
    *,
    request: Request | None,
    status_code: int,
    detail: str,
    error_code: str | None,
    qf_code: str | None,
    details: Any | None = None,
) -> dict[str, Any]:
    """Build the envelope, attach the QF code + whimsical message, and fire the
    rate-limited support notify when the resolved code opts in.

    This is the single choke-point every handler funnels through so the
    whimsical fields and the support-notify behaviour stay consistent.
    """
    spec = _resolve_spec(status_code=status_code, qf_code=qf_code)
    trace_id = _current_trace_id()
    _maybe_notify_support_for(spec, request, trace_id=trace_id)
    return build_error_envelope(
        status_code=status_code,
        detail=detail,
        error_code=error_code or ec.legacy_error_code(spec),
        trace_id=trace_id,
        details=details,
        code=spec.code,
        whimsical=spec.whimsical_message,
    )


# ---------------------------------------------------------------------------
# Global handlers
# ---------------------------------------------------------------------------


_GENERIC_5XX_DETAIL: Final[str] = (
    "An unexpected internal error occurred. Our wizards have been notified."
)


async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    body = _envelope_with_whimsy(
        request=request,
        status_code=exc.http_status,
        detail=str(exc) or default_error_code_for_status(exc.http_status),
        error_code=exc.error_code,
        qf_code=exc.qf_code,
        details=exc.details,
    )
    return JSONResponse(status_code=exc.http_status, content=body)


async def _http_exception_handler(
    request: Request, exc: HTTPException
) -> JSONResponse:
    """Wrap classic ``HTTPException`` into the unified envelope.

    The whimsical ``QF-`` code is carried on a ``.qf_code`` ATTRIBUTE of the
    exception (see :func:`coded_http_exception`) — NOT inside ``detail``. This
    keeps ``detail`` a plain human-readable STRING in the serialized envelope
    (backward-compatible: existing consumers + smoke tests read ``detail`` as a
    string), while ``code`` + ``whimsical`` are emitted as separate top-level
    envelope fields.

    A handler may still raise ``HTTPException(detail={...})`` to opt into a
    custom ``errorCode``/``code`` (legacy dict-detail form); that path is kept
    for backward-compat, but the ``.qf_code`` attribute takes precedence.
    """
    detail = exc.detail
    error_code: str | None = None
    # Whimsical: the precise QF code is preferentially read off a dedicated
    # ``.qf_code`` attribute (clean coded-exception mechanism).
    qf_code: str | None = getattr(exc, "qf_code", None)
    extra_details: Any | None = None
    detail_text: str

    if isinstance(detail, dict):
        # Legacy pre-built envelope-ish dict: pull through known keys.
        detail_text = str(detail.get("detail") or detail.get("message") or "")
        if not detail_text:
            detail_text = default_error_code_for_status(exc.status_code)
        error_code = detail.get("errorCode") or detail.get("error_code")
        qf_code = qf_code or detail.get("code") or detail.get("qf_code")
        # Preserve any extra context the handler attached (rate-limit headers, etc.).
        leftover = {
            k: v
            for k, v in detail.items()
            if k
            not in {"detail", "message", "errorCode", "error_code", "code", "qf_code"}
        }
        extra_details = jsonable_encoder(leftover) if leftover else None
    elif isinstance(detail, list):
        detail_text = default_error_code_for_status(exc.status_code)
        extra_details = jsonable_encoder(detail)
    else:
        detail_text = str(detail) if detail else default_error_code_for_status(
            exc.status_code
        )

    body = _envelope_with_whimsy(
        request=request,
        status_code=exc.status_code,
        detail=detail_text,
        error_code=error_code,
        qf_code=qf_code,
        details=extra_details,
    )
    headers = getattr(exc, "headers", None)
    return JSONResponse(status_code=exc.status_code, content=body, headers=headers)


async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    body = _envelope_with_whimsy(
        request=request,
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="Request validation failed.",
        error_code="VALIDATION_ERROR",
        qf_code=ec.QF_VALIDATION_ERROR,
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
    body = _envelope_with_whimsy(
        request=request,
        status_code=500,
        detail=_GENERIC_5XX_DETAIL,
        error_code="INTERNAL_SERVER_ERROR",
        qf_code=ec.QF_UNKNOWN,
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
    "coded_http_exception",
    "default_error_code_for_status",
    "install_error_handlers",
]
