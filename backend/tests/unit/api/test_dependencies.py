# backend/tests/unit/api/test_dependencies.py

import json
import pytest
from sqlalchemy import text
from fastapi import HTTPException, Request

from app.api import dependencies as deps
from app.core.config import settings


# --------------------------
# Test Stubs
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
    """Minimal Response stub for httpx client."""
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
    """Verify we can create an engine, get a session, run a query, and close up."""
    # Reset globals to ensure clean slate
    monkeypatch.setattr(deps, "db_engine", None, raising=False)
    monkeypatch.setattr(deps, "async_session_factory", None, raising=False)

    # Create engine/factory for in-memory sqlite
    deps.create_db_engine_and_session_maker("sqlite+aiosqlite:///:memory:")
    
    assert deps.db_engine is not None
    assert deps.async_session_factory is not None

    # Use get_db_session() generator to obtain a session
    agen = deps.get_db_session()
    session = await agen.__anext__()
    
    try:
        # Run trivial query to verify connectivity
        res = await session.execute(text("SELECT 1"))
        one = res.scalar()
        assert one == 1
    finally:
        await agen.aclose()

    # Closing global engine
    await deps.close_db_engine()

    # Clean up globals for isolation
    monkeypatch.setattr(deps, "db_engine", None, raising=False)
    monkeypatch.setattr(deps, "async_session_factory", None, raising=False)


@pytest.mark.asyncio
async def test_get_db_session_raises_when_uninitialized(monkeypatch):
    """get_db_session should raise 503 if factory is missing."""
    # Ensure factory is not set
    monkeypatch.setattr(deps, "async_session_factory", None, raising=False)

    # In the actual implementation, get_db_session raises HTTPException(503)
    with pytest.raises(HTTPException) as e:
        agen = deps.get_db_session()
        await agen.__anext__()
    
    assert e.value.status_code == 503
    assert "Database not ready" in e.value.detail


def test_create_db_engine_idempotent(monkeypatch):
    """Creating the engine twice should be a no-op."""
    monkeypatch.setattr(deps, "db_engine", None, raising=False)
    monkeypatch.setattr(deps, "async_session_factory", None, raising=False)

    deps.create_db_engine_and_session_maker("sqlite+aiosqlite:///:memory:")
    first_engine = deps.db_engine
    
    # Second call
    deps.create_db_engine_and_session_maker("sqlite+aiosqlite:///:memory:")
    assert deps.db_engine is first_engine


# ======================
# Redis pool/client tests
# ======================

@pytest.mark.asyncio
async def test_get_redis_client_raises_when_pool_missing(monkeypatch):
    """get_redis_client should raise 503 if pool is missing."""
    monkeypatch.setattr(deps, "redis_pool", None, raising=False)
    
    with pytest.raises(HTTPException) as e:
        await deps.get_redis_client()
    
    assert e.value.status_code == 503
    assert "Redis not ready" in e.value.detail


@pytest.mark.asyncio
async def test_create_redis_pool_and_get_redis_client(monkeypatch):
    """Verify pool creation and client instantiation."""
    monkeypatch.setattr(deps, "redis_pool", None, raising=False)

    # Creating the pool (lazy) should not fail
    deps.create_redis_pool("redis://localhost:6379/0")
    assert deps.redis_pool is not None

    # Getting the client
    client = await deps.get_redis_client()
    
    # Verify client uses the global pool
    # Note: redis.asyncio.Redis stores the pool in .connection_pool
    assert getattr(client, "connection_pool", None) is deps.redis_pool

    # Reset global
    monkeypatch.setattr(deps, "redis_pool", None, raising=False)


@pytest.mark.asyncio
async def test_close_redis_pool_uses_aclose_and_resets_global(monkeypatch):
    """Verify pool closure logic."""
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
    """If security.enabled is False, validation is skipped."""
    # settings.security is a Pydantic model, so we setattr on the model instance
    monkeypatch.setattr(settings.security, "enabled", False, raising=False)
    
    req = _Req(payload=None)
    ok = await deps.verify_turnstile(req)
    assert ok is True


@pytest.mark.asyncio
async def test_verify_turnstile_missing_token_raises_400(monkeypatch):
    """Missing token in payload raises 400."""
    # Enable security
    monkeypatch.setattr(settings.security, "enabled", True, raising=False)
    
    req = _Req(payload={})  # Empty body
    
    with pytest.raises(HTTPException) as e:
        await deps.verify_turnstile(req)
    assert e.value.status_code == 400
    assert "token not provided" in e.value.detail


@pytest.mark.asyncio
async def test_verify_turnstile_local_bypass_when_unconfigured(monkeypatch):
    """
    In local dev with no secret key, we should bypass (dev convenience).
    """
    monkeypatch.setattr(settings.security, "enabled", True, raising=False)
    monkeypatch.setattr(settings.app, "environment", "local", raising=False)
    # Empty secret key
    monkeypatch.setattr(settings.security.turnstile, "secret_key", "", raising=False)

    req = _Req(payload={"cf-turnstile-response": "dummy-token"})
    ok = await deps.verify_turnstile(req)
    assert ok is True


@pytest.mark.asyncio
async def test_verify_turnstile_httpx_success(monkeypatch):
    """Verify successful upstream verification."""
    monkeypatch.setattr(settings.security, "enabled", True, raising=False)
    monkeypatch.setattr(settings.app, "environment", "prod", raising=False)
    monkeypatch.setattr(settings.security.turnstile, "secret_key", "secret-123", raising=False)

    # Stub httpx to return success: true
    monkeypatch.setattr(
        deps.httpx, "AsyncClient",
        lambda: _DummyHTTPXClient({"success": True}),
        raising=False
    )

    req = _Req(payload={"cf-turnstile-response": "valid-token"})
    ok = await deps.verify_turnstile(req)
    assert ok is True


@pytest.mark.asyncio
async def test_verify_turnstile_httpx_failure_raises_401(monkeypatch):
    """Verify failed upstream verification."""
    monkeypatch.setattr(settings.security, "enabled", True, raising=False)
    monkeypatch.setattr(settings.app, "environment", "prod", raising=False)
    monkeypatch.setattr(settings.security.turnstile, "secret_key", "secret-123", raising=False)

    # Stub httpx to return success: false
    monkeypatch.setattr(
        deps.httpx, "AsyncClient",
        lambda: _DummyHTTPXClient({"success": False, "error-codes": ["bad-token"]}),
        raising=False
    )

    req = _Req(payload={"cf-turnstile-response": "invalid-token"})
    
    with pytest.raises(HTTPException) as e:
        await deps.verify_turnstile(req)
    assert e.value.status_code == 401
    assert "Invalid Turnstile token" in e.value.detail