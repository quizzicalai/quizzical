"""Iter G: every response must carry baseline OWASP-aligned security headers."""
from __future__ import annotations

import pytest


def _assert_security_headers(headers) -> None:
    assert headers.get("X-Content-Type-Options") == "nosniff"
    assert headers.get("X-Frame-Options") == "DENY"
    assert headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    permissions = headers.get("Permissions-Policy", "")
    assert "geolocation=()" in permissions
    assert "microphone=()" in permissions
    assert "camera=()" in permissions


@pytest.mark.asyncio
async def test_security_headers_present_on_health(async_client) -> None:
    r = await async_client.get("/api/v1/health")
    # Endpoint may or may not exist in test app; we only care about middleware behaviour.
    assert r.status_code in (200, 404, 503)
    _assert_security_headers(r.headers)


@pytest.mark.asyncio
async def test_security_headers_present_on_unknown_route(async_client) -> None:
    r = await async_client.get("/definitely-not-a-route")
    assert r.status_code == 404
    _assert_security_headers(r.headers)


@pytest.mark.asyncio
async def test_security_headers_present_on_options_preflight(async_client) -> None:
    r = await async_client.options(
        "/api/v1/quiz/start",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
        },
    )
    _assert_security_headers(r.headers)
