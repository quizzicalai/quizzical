"""Hardened security headers: CSP, COOP, CORP, conditional HSTS.

These complement ``test_iterG_security_headers.py`` (baseline OWASP set)
with the modern hardening headers we now emit on every response.
"""
from __future__ import annotations

import pytest


def _assert_modern_headers(headers) -> None:
    csp = headers.get("Content-Security-Policy", "")
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "base-uri 'none'" in csp
    assert headers.get("Cross-Origin-Opener-Policy") == "same-origin"
    assert headers.get("Cross-Origin-Resource-Policy") == "same-origin"


@pytest.mark.asyncio
async def test_csp_and_coop_corp_present_on_health(async_client) -> None:
    r = await async_client.get("/health")
    assert r.status_code == 200
    _assert_modern_headers(r.headers)


@pytest.mark.asyncio
async def test_csp_and_coop_corp_present_on_unknown_route(async_client) -> None:
    r = await async_client.get("/definitely-not-a-route")
    assert r.status_code == 404
    _assert_modern_headers(r.headers)


@pytest.mark.asyncio
async def test_hsts_absent_in_local_env(async_client) -> None:
    r = await async_client.get("/health")
    # In local/test env we never want to pin browsers to HTTPS.
    assert "Strict-Transport-Security" not in r.headers


@pytest.mark.asyncio
async def test_hsts_present_when_env_is_production(async_client, monkeypatch) -> None:
    """In a non-local env we must emit HSTS.

    The middleware reads ``settings.APP_ENVIRONMENT`` per request, so flipping
    ``settings.app.environment`` is enough — no module reload required (which
    would invalidate the test client's pinned ``app.main.app`` reference).
    """
    from app.core import config as core_config

    monkeypatch.setattr(core_config.settings.app, "environment", "production", raising=False)
    r = await async_client.get("/health")
    sts = r.headers.get("Strict-Transport-Security", "")
    assert "max-age=" in sts
    assert "includeSubDomains" in sts

