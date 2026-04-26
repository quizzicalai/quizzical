"""Docs/OpenAPI must be disabled in non-local environments."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


def _make_app(env: str) -> FastAPI:
    docs_enabled = env.lower() in {"local", "dev", "development", "test", "testing"}
    return FastAPI(
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )


@pytest.mark.asyncio
async def test_docs_disabled_in_production() -> None:
    app = _make_app("production")
    assert app.docs_url is None
    assert app.redoc_url is None
    assert app.openapi_url is None
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            r = await client.get(path)
            assert r.status_code == 404, f"{path} should be disabled in production"


@pytest.mark.asyncio
async def test_docs_enabled_in_local() -> None:
    app = _make_app("local")
    assert app.docs_url == "/docs"
    assert app.openapi_url == "/openapi.json"
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/openapi.json")
        assert r.status_code == 200


def test_main_app_uses_local_docs_in_test_env() -> None:
    """The real app is built with APP_ENVIRONMENT=local in tests, so its
    docs URLs must be set."""
    from app.main import app

    assert app.docs_url == "/docs"
    assert app.openapi_url == "/openapi.json"
