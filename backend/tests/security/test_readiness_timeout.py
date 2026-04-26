"""Readiness probe is bounded by a timeout so a wedged dep can't hang it."""
from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_readiness_db_timeout_returns_503(async_client, monkeypatch) -> None:
    """A hung DB ping must return 503 with reason db_timeout, not hang."""
    from app.api import dependencies as deps

    class _SlowConn:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *exc):
            return False
        async def execute(self, *_a, **_k):
            await asyncio.sleep(5)  # exceeds 2.0s default

    class _SlowEngine:
        def connect(self):
            return _SlowConn()

    monkeypatch.setattr(deps, "db_engine", _SlowEngine(), raising=False)
    monkeypatch.setenv("READINESS_PROBE_TIMEOUT_S", "0.1")
    # Re-read constant in module (it was captured at import time, so we patch the value).
    import app.main as main_mod
    monkeypatch.setattr(main_mod, "_READINESS_TIMEOUT_S", 0.1, raising=False)

    r = await asyncio.wait_for(async_client.get("/readiness"), timeout=2.0)
    assert r.status_code == 503
    assert r.json()["reason"] == "db_timeout"


@pytest.mark.asyncio
async def test_readiness_db_error_returns_503(async_client, monkeypatch) -> None:
    from app.api import dependencies as deps

    class _BoomConn:
        async def __aenter__(self):
            raise RuntimeError("db down")
        async def __aexit__(self, *exc):
            return False

    class _BoomEngine:
        def connect(self):
            return _BoomConn()

    monkeypatch.setattr(deps, "db_engine", _BoomEngine(), raising=False)
    r = await async_client.get("/readiness")
    assert r.status_code == 503
    assert r.json()["reason"] == "db"
