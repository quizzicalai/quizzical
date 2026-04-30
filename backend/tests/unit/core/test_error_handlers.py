"""Unit tests for the global exception handlers in app.core.errors.

These handlers (`_app_error_handler`, `_http_exception_handler`,
`_validation_exception_handler`, `_unhandled_exception_handler`) are mounted
once on the FastAPI app and shape every error response the FE sees, so they
deserve direct unit coverage independent of any endpoint.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
import structlog
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError

from app.core import errors


def _body(resp) -> dict[str, Any]:
    return json.loads(bytes(resp.body).decode("utf-8"))


class _Req:
    """Minimal duck-typed Request — handlers only touch .url.path."""

    class _Url:
        path = "/api/whatever"

    url = _Url()


# ---------------------------------------------------------------------------
# _current_trace_id
# ---------------------------------------------------------------------------
class TestCurrentTraceId:
    def test_returns_not_found_when_unbound(self):
        structlog.contextvars.clear_contextvars()
        assert errors._current_trace_id() == "not_found"

    def test_returns_bound_trace_id(self):
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(trace_id="abc-123")
        try:
            assert errors._current_trace_id() == "abc-123"
        finally:
            structlog.contextvars.clear_contextvars()


# ---------------------------------------------------------------------------
# _app_error_handler
# ---------------------------------------------------------------------------
class TestAppErrorHandler:
    @pytest.mark.asyncio
    async def test_renders_envelope_with_subclass_status_and_code(self):
        exc = errors.NotFoundError("missing thing")
        resp = await errors._app_error_handler(_Req(), exc)
        assert resp.status_code == 404
        body = _body(resp)
        assert body["detail"] == "missing thing"
        assert body["errorCode"] == "NOT_FOUND"
        assert "traceId" in body
        assert "details" not in body  # No details attached.

    @pytest.mark.asyncio
    async def test_includes_details_when_provided(self):
        exc = errors.ValidationFailedError("bad input", details={"field": "x"})
        resp = await errors._app_error_handler(_Req(), exc)
        body = _body(resp)
        assert body["errorCode"] == "VALIDATION_ERROR"
        assert body["details"] == {"field": "x"}

    @pytest.mark.asyncio
    async def test_empty_message_falls_back_to_canonical_code(self):
        exc = errors.ServiceUnavailableError("")
        resp = await errors._app_error_handler(_Req(), exc)
        body = _body(resp)
        assert resp.status_code == 503
        # Empty message → falls back to canonical code as the detail string.
        assert body["detail"] == "SERVICE_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_session_busy_uses_overridden_code(self):
        exc = errors.SessionBusyError("locked")
        resp = await errors._app_error_handler(_Req(), exc)
        body = _body(resp)
        assert resp.status_code == 409
        assert body["errorCode"] == "SESSION_BUSY"


# ---------------------------------------------------------------------------
# _http_exception_handler
# ---------------------------------------------------------------------------
class TestHttpExceptionHandler:
    @pytest.mark.asyncio
    async def test_string_detail_passthrough(self):
        exc = HTTPException(status_code=418, detail="I'm a teapot")
        resp = await errors._http_exception_handler(_Req(), exc)
        body = _body(resp)
        assert resp.status_code == 418
        assert body["detail"] == "I'm a teapot"
        # 418 not in canonical map → 4xx defaults to BAD_REQUEST.
        assert body["errorCode"] == "BAD_REQUEST"

    @pytest.mark.asyncio
    async def test_dict_detail_with_explicit_error_code(self):
        exc = HTTPException(
            status_code=429,
            detail={"detail": "slow down", "errorCode": "RATE_LIMITED", "retryAfter": 30},
        )
        resp = await errors._http_exception_handler(_Req(), exc)
        body = _body(resp)
        assert body["detail"] == "slow down"
        assert body["errorCode"] == "RATE_LIMITED"
        assert body["details"] == {"retryAfter": 30}

    @pytest.mark.asyncio
    async def test_dict_detail_uses_message_key_when_detail_absent(self):
        exc = HTTPException(
            status_code=409,
            detail={"message": "conflict-y", "error_code": "SESSION_BUSY"},
        )
        resp = await errors._http_exception_handler(_Req(), exc)
        body = _body(resp)
        assert body["detail"] == "conflict-y"
        assert body["errorCode"] == "SESSION_BUSY"

    @pytest.mark.asyncio
    async def test_empty_dict_detail_falls_back_to_canonical(self):
        exc = HTTPException(status_code=404, detail={})
        resp = await errors._http_exception_handler(_Req(), exc)
        body = _body(resp)
        assert body["detail"] == "NOT_FOUND"
        assert body["errorCode"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_list_detail_attached_as_details(self):
        exc = HTTPException(status_code=422, detail=[{"loc": ["x"], "msg": "bad"}])
        resp = await errors._http_exception_handler(_Req(), exc)
        body = _body(resp)
        assert body["detail"] == "VALIDATION_ERROR"
        assert body["details"] == [{"loc": ["x"], "msg": "bad"}]

    @pytest.mark.asyncio
    async def test_falsy_detail_uses_canonical_code(self):
        # HTTPException auto-fills detail with the reason phrase when None is
        # passed, so to exercise the empty-detail branch we construct the
        # exception and then forcibly clear .detail.
        exc = HTTPException(status_code=500, detail="placeholder")
        exc.detail = ""  # type: ignore[assignment]
        resp = await errors._http_exception_handler(_Req(), exc)
        body = _body(resp)
        assert body["detail"] == "INTERNAL_SERVER_ERROR"

    @pytest.mark.asyncio
    async def test_headers_propagated(self):
        exc = HTTPException(
            status_code=429, detail="slow", headers={"Retry-After": "60"}
        )
        resp = await errors._http_exception_handler(_Req(), exc)
        assert resp.headers.get("retry-after") == "60"


# ---------------------------------------------------------------------------
# _validation_exception_handler
# ---------------------------------------------------------------------------
class TestValidationExceptionHandler:
    @pytest.mark.asyncio
    async def test_renders_422_envelope_with_pydantic_errors(self):
        class _Model(BaseModel):
            x: int

        try:
            _Model(x="not-an-int")  # type: ignore[arg-type]
        except ValidationError as ve:
            errs = ve.errors()
        exc = RequestValidationError(errors=errs)
        resp = await errors._validation_exception_handler(_Req(), exc)
        body = _body(resp)
        assert resp.status_code == 422
        assert body["errorCode"] == "VALIDATION_ERROR"
        assert body["detail"] == "Request validation failed."
        assert isinstance(body["details"], list) and body["details"]


# ---------------------------------------------------------------------------
# _unhandled_exception_handler
# ---------------------------------------------------------------------------
class TestUnhandledExceptionHandler:
    @pytest.mark.asyncio
    async def test_returns_500_with_generic_envelope(self):
        exc = RuntimeError("boom")
        resp = await errors._unhandled_exception_handler(_Req(), exc)
        body = _body(resp)
        assert resp.status_code == 500
        assert body["errorCode"] == "INTERNAL_SERVER_ERROR"
        # Generic message — does NOT leak the original exception text.
        assert "boom" not in body["detail"]
        assert "wizards" in body["detail"].lower()


# ---------------------------------------------------------------------------
# default_error_code_for_status
# ---------------------------------------------------------------------------
class TestDefaultErrorCodeForStatus:
    @pytest.mark.parametrize("code,expected", [
        (400, "BAD_REQUEST"),
        (401, "UNAUTHORIZED"),
        (403, "FORBIDDEN"),
        (404, "NOT_FOUND"),
        (409, "CONFLICT"),
        (413, "PAYLOAD_TOO_LARGE"),
        (422, "VALIDATION_ERROR"),
        (429, "RATE_LIMITED"),
        (500, "INTERNAL_SERVER_ERROR"),
        (503, "SERVICE_UNAVAILABLE"),
    ])
    def test_canonical_mapping(self, code, expected):
        assert errors.default_error_code_for_status(code) == expected

    def test_other_4xx_falls_back_to_bad_request(self):
        assert errors.default_error_code_for_status(418) == "BAD_REQUEST"
        assert errors.default_error_code_for_status(451) == "BAD_REQUEST"

    def test_other_5xx_falls_back_to_internal(self):
        assert errors.default_error_code_for_status(502) == "INTERNAL_SERVER_ERROR"
        assert errors.default_error_code_for_status(599) == "INTERNAL_SERVER_ERROR"


# ---------------------------------------------------------------------------
# install_error_handlers
# ---------------------------------------------------------------------------
class TestInstallErrorHandlers:
    def test_registers_all_four_handlers(self):
        from fastapi import FastAPI

        app = FastAPI()
        errors.install_error_handlers(app)
        # FastAPI stores handlers on .exception_handlers (a dict).
        registered = set(app.exception_handlers.keys())
        assert errors.AppError in registered
        assert HTTPException in registered
        assert RequestValidationError in registered
        assert Exception in registered
