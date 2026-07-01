"""Whimsical-error envelope wiring (owner request, 2026-06-30).

The unified envelope now carries ``code`` (the QF code) + ``whimsical`` (the
user-facing message) ALONGSIDE the existing ``detail``/``errorCode``/``traceId``.
These tests prove the additive fields are populated correctly and that the
support-notify side-effect fires (rate-limited) for notify codes — without
breaking the existing contract.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

import app.core.error_codes as ec

pytestmark = pytest.mark.anyio


def _build_app(monkeypatch) -> tuple[FastAPI, list[str]]:
    """A tiny app whose handlers exercise each envelope path. Captures the
    codes the support-notify choke-point was asked to notify for."""
    from app.core.errors import (
        AppError,
        NotFoundError,
        SessionBusyError,
        install_error_handlers,
    )

    notified: list[str] = []

    def _fake_notify(spec, *, trace_id=None, path=None, context=None):
        notified.append(spec.code)

    # Patch where the envelope choke-point imports it.
    import app.services.support_notify as sn

    monkeypatch.setattr(sn, "maybe_notify_support", _fake_notify)

    app = FastAPI()
    install_error_handlers(app)

    @app.get("/not_found")
    async def _nf() -> None:
        raise NotFoundError("Quiz session not found.")

    @app.get("/busy")
    async def _busy() -> None:
        raise SessionBusyError("locked")

    @app.get("/unhandled")
    async def _unh() -> None:
        raise RuntimeError("oh no internal detail")

    @app.get("/http_pinned")
    async def _pinned() -> None:
        raise HTTPException(
            status_code=503,
            detail={"detail": "at capacity", "code": ec.QF_COST_CEILING},
        )

    @app.get("/http_plain")
    async def _plain() -> None:
        raise HTTPException(status_code=404, detail="missing widget")

    @app.get("/app_error_with_code")
    async def _awc() -> None:
        raise AppError("boom", qf_code=ec.QF_LLM_PROVIDER_DOWN)

    return app, notified


async def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    )


async def test_app_error_envelope_has_code_and_whimsical(monkeypatch) -> None:
    app, _ = _build_app(monkeypatch)
    async with await _client(app) as ac:
        resp = await ac.get("/not_found")
    assert resp.status_code == 404
    body = resp.json()
    # Backward-compat fields preserved.
    assert body["errorCode"] == "NOT_FOUND"
    assert body["detail"] == "Quiz session not found."
    assert "traceId" in body
    # New whimsical fields present.
    assert body["code"] == ec.QF_QUIZ_NOT_FOUND
    assert body["whimsical"] == ec.get_spec(ec.QF_QUIZ_NOT_FOUND).whimsical_message


async def test_session_busy_maps_to_qf_session_busy(monkeypatch) -> None:
    app, _ = _build_app(monkeypatch)
    async with await _client(app) as ac:
        resp = await ac.get("/busy")
    body = resp.json()
    assert resp.status_code == 409
    assert body["errorCode"] == "SESSION_BUSY"
    assert body["code"] == ec.QF_SESSION_BUSY


async def test_unhandled_500_uses_catch_all_and_hides_detail(monkeypatch) -> None:
    app, notified = _build_app(monkeypatch)
    async with await _client(app) as ac:
        resp = await ac.get("/unhandled")
    body = resp.json()
    assert resp.status_code == 500
    assert body["code"] == ec.QF_UNKNOWN
    assert body["errorCode"] == "INTERNAL_SERVER_ERROR"
    # Never leak the raw internal message.
    assert "oh no internal detail" not in body["detail"]
    assert "oh no internal detail" not in body["whimsical"]
    # QF-UNKNOWN is a notify code → support was alerted.
    assert ec.QF_UNKNOWN in notified


async def test_http_exception_dict_code_is_honoured(monkeypatch) -> None:
    app, notified = _build_app(monkeypatch)
    async with await _client(app) as ac:
        resp = await ac.get("/http_pinned")
    body = resp.json()
    assert resp.status_code == 503
    assert body["code"] == ec.QF_COST_CEILING
    assert body["detail"] == "at capacity"
    # QF-COST-CEILING notifies support.
    assert ec.QF_COST_CEILING in notified


async def test_plain_http_exception_derives_code_from_status(monkeypatch) -> None:
    app, notified = _build_app(monkeypatch)
    async with await _client(app) as ac:
        resp = await ac.get("/http_plain")
    body = resp.json()
    assert resp.status_code == 404
    # No pinned code → derived from status (404 → quiz-not-found).
    assert body["code"] == ec.QF_QUIZ_NOT_FOUND
    assert body["detail"] == "missing widget"  # original detail preserved
    # 404 quiz-not-found is NOT a notify code.
    assert ec.QF_QUIZ_NOT_FOUND not in notified


async def test_app_error_explicit_qf_code(monkeypatch) -> None:
    app, notified = _build_app(monkeypatch)
    async with await _client(app) as ac:
        resp = await ac.get("/app_error_with_code")
    body = resp.json()
    assert body["code"] == ec.QF_LLM_PROVIDER_DOWN
    # provider-down notifies support.
    assert ec.QF_LLM_PROVIDER_DOWN in notified


async def test_non_notify_codes_do_not_alert(monkeypatch) -> None:
    app, notified = _build_app(monkeypatch)
    async with await _client(app) as ac:
        await ac.get("/not_found")
        await ac.get("/busy")
    # Neither NOT_FOUND nor SESSION_BUSY should have paged support.
    assert notified == []
