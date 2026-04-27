# tests/unit/security/test_trusted_host.py
"""§15.2 — Trusted Host enforcement (AC-HOST-1..3)."""
from __future__ import annotations

import importlib

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


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
