"""Iter H: verify_turnstile must coerce non-dict JSON bodies to "no token" (400),
not crash with AttributeError (500)."""
from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def turnstile_app(monkeypatch) -> FastAPI:
    """Mount a tiny app whose only dep is verify_turnstile, and force it on."""
    from types import SimpleNamespace

    from app.api import dependencies as deps

    fake = SimpleNamespace(
        ENABLE_TURNSTILE=True,
        APP_ENVIRONMENT="production",
        TURNSTILE_SECRET_KEY="real-secret",
    )
    monkeypatch.setattr(deps, "settings", fake)

    app = FastAPI()

    @app.post("/probe")
    async def probe(_: bool = Depends(deps.verify_turnstile)) -> dict:
        return {"ok": True}

    return app


@pytest.mark.parametrize("body", [[], "oops", 42, None])
def test_verify_turnstile_rejects_non_dict_body_with_400(turnstile_app: FastAPI, body) -> None:
    """Non-dict JSON bodies must be treated as a missing token (400), not a server error (500)."""
    client = TestClient(turnstile_app, raise_server_exceptions=False)
    resp = client.post("/probe", json=body)
    assert resp.status_code == 400, (
        f"non-dict body {body!r} should yield 400, got {resp.status_code} body={resp.text!r}"
    )
    assert "token" in resp.text.lower()


def test_verify_turnstile_rejects_dict_without_token_with_400(turnstile_app: FastAPI) -> None:
    client = TestClient(turnstile_app, raise_server_exceptions=False)
    resp = client.post("/probe", json={"some": "field"})
    assert resp.status_code == 400
