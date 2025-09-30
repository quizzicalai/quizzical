# backend/tests/unit/api/test_dependencies.py

import json
import pytest
from sqlalchemy import text

from app.api import dependencies as deps
from app.core.config import settings


# --------------------------
# Small file-local test stubs
# --------------------------

class _Req:
    """Minimal Request stub with a configurable body()."""
    def __init__(self, payload: dict | None):
        self._payload = payload

    async def body(self) -> bytes:
        if self._payload is None:
            return b""
        return json.dumps(self._payload).encode("utf-8")


class _DummyResp:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _DummyHTTPXClient:
    """AsyncClient stub that returns a configurable response."""
    def __init__(self, reply: dict):
        self._reply = reply

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, *_args, **_kwargs):
        return _DummyResp(self._reply)


# =========================
# DB engine / session tests
# =========================

@pytest.mark.asyncio
async def test_create_db_engine_and_session_maker_sqlite_and_get_db_session_ok(monkeypatch):
    # Reset globals
    monkeypatch.setattr(deps, "db_engine", None, raising=False)
    monkeypatch.setattr(deps, "async_session_factory", None, raising=False)

    # Create engine/factory for in-memory sqlite
    deps.create_db_engine_and_session_maker("sqlite+aiosqlite:///:memory:")
    assert deps.db_engine is not None
    assert deps.async_session_factory is not None

    # Use get_db_session() to obtain a session and run a trivial query
    agen = deps.get_db_session()
    session = await agen.__anext__()
    try:
        res = await session.execute(text("SELECT 1"))
        one = res.scalar()
        assert one == 1
    finally:
        await agen.aclose()

    # Closing should not raise
    await deps.close_db_engine()

    # Clean up globals for isolation
    monkeypatch.setattr(deps, "db_engine", None, raising=False)
    monkeypatch.setattr(deps, "async_session_factory", None, raising=False)


@pytest.mark.asyncio
async def test_get_db_session_raises_when_uninitialized(monkeypatch):
    # Ensure factory is not set
    monkeypatch.setattr(deps, "async_session_factory", None, raising=False)

    with pytest.raises(RuntimeError):
        agen = deps.get_db_session()
        try:
            await agen.__anext__()
        finally:
            # If it somehow yielded, close gracefully
            try:
                await agen.aclose()
            except Exception:
                pass


def test_create_db_engine_idempotent(monkeypatch):
    # Reset globals
    monkeypatch.setattr(deps, "db_engine", None, raising=False)
    monkeypatch.setattr(deps, "async_session_factory", None, raising=False)

    deps.create_db_engine_and_session_maker("sqlite+aiosqlite:///:memory:")
    first = deps.db_engine
    # Second call is a no-op
    deps.create_db_engine_and_session_maker("sqlite+aiosqlite:///:memory:")
    assert deps.db_engine is first


# ======================
# Redis pool/client tests
# ======================

@pytest.mark.asyncio
async def test_get_redis_client_raises_when_pool_missing(monkeypatch):
    monkeypatch.setattr(deps, "redis_pool", None, raising=False)
    with pytest.raises(RuntimeError):
        await deps.get_redis_client()


@pytest.mark.asyncio
async def test_create_redis_pool_and_get_redis_client(monkeypatch):
    # Start clean
    monkeypatch.setattr(deps, "redis_pool", None, raising=False)

    # Creating the pool should not attempt a network connection
    deps.create_redis_pool("redis://localhost:6379/0")
    assert deps.redis_pool is not None

    client = await deps.get_redis_client()
    # The client should be a redis.asyncio Redis with our pool attached
    assert getattr(client, "connection_pool", None) is deps.redis_pool

    # Don't actually close a real pool here; just reset for isolation
    monkeypatch.setattr(deps, "redis_pool", None, raising=False)


@pytest.mark.asyncio
async def test_close_redis_pool_uses_aclose_and_resets_global(monkeypatch):
    called = {"n": 0}

    class _PoolStub:
        async def aclose(self):
            called["n"] += 1

    monkeypatch.setattr(deps, "redis_pool", _PoolStub(), raising=False)
    await deps.close_redis_pool()
    assert called["n"] == 1
    assert deps.redis_pool is None


# =======================
# Turnstile verification
# =======================

@pytest.mark.asyncio
async def test_verify_turnstile_bypass_when_disabled(monkeypatch):
    # Force global bypass
    monkeypatch.setattr(settings.security, "enabled", False, raising=False)
    req = _Req(payload=None)
    ok = await deps.verify_turnstile(req)
    assert ok is True


@pytest.mark.asyncio
async def test_verify_turnstile_missing_token_raises_400_in_prod(monkeypatch):
    from fastapi import HTTPException

    # Enabled, prod-like env, secret configured
    monkeypatch.setattr(settings.security, "enabled", True, raising=False)
    monkeypatch.setattr(settings.app, "environment", "prod", raising=False)
    monkeypatch.setattr(settings.security.turnstile, "secret_key", "abc123", raising=False)

    req = _Req(payload={})  # no token
    with pytest.raises(HTTPException) as e:
        await deps.verify_turnstile(req)
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_verify_turnstile_local_bypass_when_unconfigured(monkeypatch):
    # Enabled but local and unconfigured -> bypass after token is present
    monkeypatch.setattr(settings.security, "enabled", True, raising=False)
    monkeypatch.setattr(settings.app, "environment", "local", raising=False)
    monkeypatch.setattr(settings.security.turnstile, "secret_key", "", raising=False)

    req = _Req(payload={"cf-turnstile-response": "dummy"})
    ok = await deps.verify_turnstile(req)
    assert ok is True


@pytest.mark.asyncio
async def test_verify_turnstile_httpx_success(monkeypatch):
    # Enabled, prod-like, with secret -> should call httpx and accept success
    monkeypatch.setattr(settings.security, "enabled", True, raising=False)
    monkeypatch.setattr(settings.app, "environment", "prod", raising=False)
    monkeypatch.setattr(settings.security.turnstile, "secret_key", "abc123", raising=False)

    # Stub httpx.AsyncClient to return success
    monkeypatch.setattr(
        deps.httpx, "AsyncClient",
        lambda: _DummyHTTPXClient({"success": True}),
        raising=False
    )

    req = _Req(payload={"cf-turnstile-response": "tok"})
    ok = await deps.verify_turnstile(req)
    assert ok is True


@pytest.mark.asyncio
async def test_verify_turnstile_httpx_failure_raises_401(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(settings.security, "enabled", True, raising=False)
    monkeypatch.setattr(settings.app, "environment", "prod", raising=False)
    monkeypatch.setattr(settings.security.turnstile, "secret_key", "abc123", raising=False)

    # Stub httpx.AsyncClient to return failure
    monkeypatch.setattr(
        deps.httpx, "AsyncClient",
        lambda: _DummyHTTPXClient({"success": False, "error-codes": ["bad"]}),
        raising=False
    )

    req = _Req(payload={"cf-turnstile-response": "tok"})
    with pytest.raises(HTTPException) as e:
        await deps.verify_turnstile(req)
    assert e.value.status_code == 401
