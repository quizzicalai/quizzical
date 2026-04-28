"""§18.1 / §18.2 — Unified API error envelope (AC-QUALITY-ERR-*, AC-QUALITY-EXC-*)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# AppError hierarchy (unit)
# ---------------------------------------------------------------------------

def test_app_error_defaults_to_500_internal_server_error() -> None:
    from app.core.errors import AppError

    err = AppError("boom")
    assert err.http_status == 500
    assert err.error_code == "INTERNAL_SERVER_ERROR"
    assert str(err) == "boom"
    assert err.details is None


def test_not_found_error_carries_404_and_code() -> None:
    from app.core.errors import NotFoundError

    err = NotFoundError("missing", details={"id": "abc"})
    assert err.http_status == 404
    assert err.error_code == "NOT_FOUND"
    assert err.details == {"id": "abc"}


def test_session_busy_inherits_conflict_with_custom_code() -> None:
    from app.core.errors import ConflictError, SessionBusyError

    err = SessionBusyError("locked")
    assert isinstance(err, ConflictError)
    assert err.http_status == 409
    assert err.error_code == "SESSION_BUSY"


def test_validation_failed_error_uses_422() -> None:
    from app.core.errors import ValidationFailedError

    err = ValidationFailedError("bad", details=[{"loc": ["body", "x"]}])
    assert err.http_status == 422
    assert err.error_code == "VALIDATION_ERROR"
    assert err.details == [{"loc": ["body", "x"]}]


# ---------------------------------------------------------------------------
# Envelope serialiser (unit)
# ---------------------------------------------------------------------------

def test_envelope_from_http_status_maps_known_codes() -> None:
    from app.core.errors import default_error_code_for_status

    assert default_error_code_for_status(400) == "BAD_REQUEST"
    assert default_error_code_for_status(401) == "UNAUTHORIZED"
    assert default_error_code_for_status(403) == "FORBIDDEN"
    assert default_error_code_for_status(404) == "NOT_FOUND"
    assert default_error_code_for_status(409) == "CONFLICT"
    assert default_error_code_for_status(413) == "PAYLOAD_TOO_LARGE"
    assert default_error_code_for_status(422) == "VALIDATION_ERROR"
    assert default_error_code_for_status(429) == "RATE_LIMITED"
    assert default_error_code_for_status(500) == "INTERNAL_SERVER_ERROR"
    assert default_error_code_for_status(503) == "SERVICE_UNAVAILABLE"
    # Unknown status defaults to a generic class-tier code.
    assert default_error_code_for_status(418) == "BAD_REQUEST"  # 4xx → client_error
    assert default_error_code_for_status(599) == "INTERNAL_SERVER_ERROR"  # 5xx → server


def test_build_envelope_keeps_explicit_error_code() -> None:
    from app.core.errors import build_error_envelope

    body = build_error_envelope(
        status_code=409,
        detail="Another request is currently being processed for this session.",
        error_code="SESSION_BUSY",
        trace_id="abc-123",
    )
    assert body == {
        "detail": "Another request is currently being processed for this session.",
        "errorCode": "SESSION_BUSY",
        "traceId": "abc-123",
    }


def test_build_envelope_includes_optional_details() -> None:
    from app.core.errors import build_error_envelope

    body = build_error_envelope(
        status_code=422,
        detail="Validation failed.",
        error_code="VALIDATION_ERROR",
        trace_id="t",
        details=[{"loc": ["body", "category"], "msg": "field required"}],
    )
    assert body["details"] == [{"loc": ["body", "category"], "msg": "field required"}]


def test_build_envelope_omits_details_when_none() -> None:
    from app.core.errors import build_error_envelope

    body = build_error_envelope(
        status_code=404, detail="x", error_code="NOT_FOUND", trace_id="t"
    )
    assert "details" not in body


# ---------------------------------------------------------------------------
# Global handlers wired into a tiny app (integration)
# ---------------------------------------------------------------------------

def _build_test_app() -> FastAPI:
    from app.core.errors import (
        AppError,
        NotFoundError,
        SessionBusyError,
        install_error_handlers,
    )

    app = FastAPI()
    install_error_handlers(app)

    @app.get("/raises_app_error")
    async def _raises_app_error() -> dict[str, str]:
        raise NotFoundError("Quiz session not found.", details={"quiz_id": "x"})

    @app.get("/raises_session_busy")
    async def _raises_session_busy() -> dict[str, str]:
        raise SessionBusyError("locked")

    @app.get("/raises_unhandled")
    async def _raises_unhandled() -> dict[str, str]:
        raise RuntimeError("oh no")

    @app.get("/raises_app_error_no_details")
    async def _raises_no_details() -> dict[str, str]:
        raise AppError("generic")

    return app


async def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    )


async def test_app_error_returns_envelope_with_correct_fields() -> None:
    app = _build_test_app()
    async with await _client(app) as ac:
        resp = await ac.get("/raises_app_error")
    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"] == "Quiz session not found."
    assert body["errorCode"] == "NOT_FOUND"
    assert body["details"] == {"quiz_id": "x"}
    assert "traceId" in body


async def test_session_busy_envelope_uses_custom_error_code() -> None:
    app = _build_test_app()
    async with await _client(app) as ac:
        resp = await ac.get("/raises_session_busy")
    assert resp.status_code == 409
    body = resp.json()
    assert body["errorCode"] == "SESSION_BUSY"
    assert body["detail"] == "locked"


async def test_unhandled_exception_returns_500_envelope() -> None:
    app = _build_test_app()
    async with await _client(app) as ac:
        resp = await ac.get("/raises_unhandled")
    assert resp.status_code == 500
    body = resp.json()
    assert body["errorCode"] == "INTERNAL_SERVER_ERROR"
    # Never leak internal error message verbatim.
    assert "oh no" not in body["detail"]
    assert "traceId" in body


async def test_validation_error_returns_envelope_with_details() -> None:
    """RequestValidationError → 422 with errorCode=VALIDATION_ERROR."""
    from pydantic import BaseModel

    from app.core.errors import install_error_handlers

    app = FastAPI()
    install_error_handlers(app)

    class Body(BaseModel):
        category: str

    @app.post("/needs_body")
    async def _ep(body: Body) -> dict[str, str]:
        return {"ok": "1"}

    async with await _client(app) as ac:
        resp = await ac.post("/needs_body", json={})
    assert resp.status_code == 422
    body = resp.json()
    assert body["errorCode"] == "VALIDATION_ERROR"
    assert isinstance(body.get("details"), list)
    flat_locs = [str(item.get("loc", "")) for item in body["details"]]
    # The body field is missing; details surfaces the pydantic error list verbatim.
    assert flat_locs, body


async def test_http_exception_with_string_detail_gets_envelope() -> None:
    """Plain HTTPException(404, "x") still produces the unified envelope."""
    from fastapi import HTTPException

    from app.core.errors import install_error_handlers

    app = FastAPI()
    install_error_handlers(app)

    @app.get("/raises_http")
    async def _ep() -> None:
        raise HTTPException(status_code=404, detail="missing widget")

    async with await _client(app) as ac:
        resp = await ac.get("/raises_http")
    assert resp.status_code == 404
    body = resp.json()
    assert body["detail"] == "missing widget"
    assert body["errorCode"] == "NOT_FOUND"
    assert "traceId" in body


async def test_http_exception_with_dict_detail_preserves_existing_error_code() -> None:
    """A handler that raises HTTPException(detail={"errorCode": "SESSION_BUSY", ...})
    must NOT have its custom errorCode overwritten."""
    from fastapi import HTTPException

    from app.core.errors import install_error_handlers

    app = FastAPI()
    install_error_handlers(app)

    @app.get("/raises_dict_detail")
    async def _ep() -> None:
        raise HTTPException(
            status_code=409,
            detail={
                "detail": "Another request is currently being processed for this session.",
                "errorCode": "SESSION_BUSY",
            },
        )

    async with await _client(app) as ac:
        resp = await ac.get("/raises_dict_detail")
    assert resp.status_code == 409
    body = resp.json()
    assert body["errorCode"] == "SESSION_BUSY"
    assert "currently being processed" in body["detail"]
