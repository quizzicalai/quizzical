"""CORS hardening: explicit method/header allow-lists, no wildcards with credentials."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_cors_preflight_allows_known_origin(async_client) -> None:
    r = await async_client.options(
        "/api/quiz/start",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-turnstile-token",
        },
    )
    # Starlette returns 200 for accepted preflights.
    assert r.status_code == 200, r.text
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"
    methods = r.headers.get("access-control-allow-methods", "")
    assert "POST" in methods and "*" not in methods
    allowed = r.headers.get("access-control-allow-headers", "").lower()
    assert "content-type" in allowed
    assert "x-turnstile-token" in allowed
    assert "*" not in allowed
    # Credentials must be true since the FE uses cookies.
    assert r.headers.get("access-control-allow-credentials") == "true"


@pytest.mark.asyncio
async def test_cors_blocks_unknown_origin(async_client) -> None:
    r = await async_client.options(
        "/api/quiz/start",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "POST",
        },
    )
    # Starlette responds with 400 and no ACAO header for disallowed origins.
    assert r.headers.get("access-control-allow-origin") in (None, "")


@pytest.mark.asyncio
async def test_cors_disallowed_method_blocked(async_client) -> None:
    r = await async_client.options(
        "/api/quiz/start",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "PUT",
        },
    )
    methods = r.headers.get("access-control-allow-methods", "")
    assert "PUT" not in methods
