# tests/unit/security/test_trusted_host.py
"""§15.2 — Trusted Host enforcement (AC-HOST-1..3)."""
from __future__ import annotations

import importlib

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def _restore_main_after_reload():
    """These tests importlib.reload(app.main) to re-evaluate env-driven
    middleware, which MUTATES the shared module (leaving TrustedHostMiddleware
    installed). Restore a clean local-env module afterward so the leftover
    middleware can't 400 later tests that use Host: testserver."""
    yield
    import importlib
    import os

    os.environ["APP_ENVIRONMENT"] = "local"
    os.environ.pop("TRUSTED_HOSTS", None)
    import app.main as m

    importlib.reload(m)


async def _client_with_env(monkeypatch, env: str, hosts: str | None) -> AsyncClient:
    monkeypatch.setenv("APP_ENVIRONMENT", env)
    if hosts is None:
        monkeypatch.delenv("TRUSTED_HOSTS", raising=False)
    else:
        monkeypatch.setenv("TRUSTED_HOSTS", hosts)
    # Reload main to re-evaluate env-driven middleware setup.
    import app.main as m
    importlib.reload(m)
    return AsyncClient(transport=ASGITransport(app=m.app), base_url="http://testserver")


# AC-HOST-1: production + bad host -> 400
async def test_production_rejects_untrusted_host(monkeypatch):
    async with await _client_with_env(monkeypatch, env="production", hosts="api.example.com") as client:
        r = await client.get("/health", headers={"Host": "evil.example.com"})
        assert r.status_code == 400


# AC-HOST-2: local env -> wildcard allowed
async def test_local_env_allows_any_host(monkeypatch):
    async with await _client_with_env(monkeypatch, env="local", hosts=None) as client:
        r = await client.get("/health", headers={"Host": "anywhere.example.com"})
        assert r.status_code == 200


# AC-HOST-3: explicit allowlist permits the configured host
async def test_production_allows_listed_host(monkeypatch):
    async with await _client_with_env(monkeypatch, env="production", hosts="api.example.com") as client:
        r = await client.get("/health", headers={"Host": "api.example.com"})
        assert r.status_code == 200


# P1: the deployed env ("azure") with NO explicit TRUSTED_HOSTS must still
# install Host validation (was ["*"] => skipped). The safe default allows the
# Container Apps ingress FQDN + configured origins and rejects spoofed hosts.
async def test_azure_env_installs_safe_default_without_explicit_trusted_hosts(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", '["https://quafel.com"]')
    async with await _client_with_env(monkeypatch, env="azure", hosts=None) as client:
        ok_fqdn = await client.get(
            "/health",
            headers={"Host": "api-quizzical-dev.whitesea-815b33ea.westus2.azurecontainerapps.io"},
        )
        assert ok_fqdn.status_code == 200  # *.azurecontainerapps.io wildcard
        ok_origin = await client.get("/health", headers={"Host": "quafel.com"})
        assert ok_origin.status_code == 200  # derived from ALLOWED_ORIGINS
        ok_loopback = await client.get("/health", headers={"Host": "127.0.0.1"})
        assert ok_loopback.status_code == 200  # health probe
        bad = await client.get("/health", headers={"Host": "evil.example.com"})
        assert bad.status_code == 400
