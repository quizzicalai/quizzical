"""
Iteration 2 — Reliability: app lifespan, CORS parsing, exception handler, and
readiness/health behavior under simulated dependency failures.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import main as app_main

LOCAL_DEFAULT_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
]


# ---------------------------------------------------------------------------
# CORS allowed-origins parsing
# ---------------------------------------------------------------------------

def test_read_allowed_origins_default_when_unset(monkeypatch):
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    assert app_main._read_allowed_origins() == LOCAL_DEFAULT_ORIGINS


def test_read_allowed_origins_default_when_blank(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "   ")
    assert app_main._read_allowed_origins() == LOCAL_DEFAULT_ORIGINS


def test_read_allowed_origins_csv(monkeypatch):
    monkeypatch.setenv(
        "ALLOWED_ORIGINS", "https://a.example, https://b.example , https://c.example"
    )
    assert app_main._read_allowed_origins() == [
        "https://a.example",
        "https://b.example",
        "https://c.example",
        *LOCAL_DEFAULT_ORIGINS,
    ]


def test_read_allowed_origins_json_array(monkeypatch):
    monkeypatch.setenv(
        "ALLOWED_ORIGINS", '["https://a.example", "https://b.example"]'
    )
    assert app_main._read_allowed_origins() == [
        "https://a.example",
        "https://b.example",
        *LOCAL_DEFAULT_ORIGINS,
    ]


def test_read_allowed_origins_malformed_json_falls_back(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "[not-json")
    # Falls back to safe defaults rather than crashing
    assert app_main._read_allowed_origins() == LOCAL_DEFAULT_ORIGINS


def test_read_allowed_origins_bracketed_unquoted_entries(monkeypatch):
    monkeypatch.setenv(
        "ALLOWED_ORIGINS", "[https://a.example, https://b.example/]"
    )
    assert app_main._read_allowed_origins() == [
        "https://a.example",
        "https://b.example",
        *LOCAL_DEFAULT_ORIGINS,
    ]


# ---------------------------------------------------------------------------
# Lifespan init helpers — local-env tolerance vs prod strictness
# ---------------------------------------------------------------------------

def test_init_db_swallows_failure_in_local(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(
        app_main,
        "create_db_engine_and_session_maker",
        MagicMock(side_effect=RuntimeError("boom")),
    )
    # Must NOT raise in local
    app_main._init_db(logger, "local")
    logger.error.assert_called()


def test_init_db_raises_in_production(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(
        app_main,
        "create_db_engine_and_session_maker",
        MagicMock(side_effect=RuntimeError("boom")),
    )
    with pytest.raises(RuntimeError):
        app_main._init_db(logger, "production")


def test_init_redis_swallows_failure_in_local(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(
        app_main, "create_redis_pool", MagicMock(side_effect=RuntimeError("boom"))
    )
    app_main._init_redis(logger, "local")
    logger.error.assert_called()


def test_init_redis_raises_in_production(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(
        app_main, "create_redis_pool", MagicMock(side_effect=RuntimeError("boom"))
    )
    with pytest.raises(RuntimeError):
        app_main._init_redis(logger, "production")


@pytest.mark.asyncio
async def test_init_agent_graph_swallows_failure_in_local(monkeypatch):
    logger = MagicMock()
    fake_app = MagicMock()
    fake_app.state = MagicMock()
    monkeypatch.setattr(
        app_main, "create_agent_graph", AsyncMock(side_effect=RuntimeError("boom"))
    )
    await app_main._init_agent_graph(fake_app, logger, "local")
    logger.error.assert_called()


@pytest.mark.asyncio
async def test_init_agent_graph_raises_in_production(monkeypatch):
    logger = MagicMock()
    fake_app = MagicMock()
    fake_app.state = MagicMock()
    monkeypatch.setattr(
        app_main, "create_agent_graph", AsyncMock(side_effect=RuntimeError("boom"))
    )
    with pytest.raises(RuntimeError):
        await app_main._init_agent_graph(fake_app, logger, "production")


# ---------------------------------------------------------------------------
# Shutdown — must complete even if individual closers raise
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_shutdown_resources_tolerates_errors(monkeypatch):
    logger = MagicMock()
    fake_app = MagicMock()
    fake_app.state = MagicMock()
    fake_app.state.agent_graph = MagicMock()

    monkeypatch.setattr(
        app_main, "aclose_agent_graph", AsyncMock(side_effect=RuntimeError("graph"))
    )
    monkeypatch.setattr(
        app_main, "close_db_engine", AsyncMock(side_effect=RuntimeError("db"))
    )
    monkeypatch.setattr(
        app_main, "close_redis_pool", AsyncMock(side_effect=RuntimeError("redis"))
    )

    # Must NOT raise
    await app_main._shutdown_resources(fake_app, logger)
    # All three failures should be logged at warning level
    assert logger.warning.call_count >= 3


@pytest.mark.asyncio
async def test_shutdown_resources_no_agent_graph_is_ok(monkeypatch):
    logger = MagicMock()
    fake_app = MagicMock()
    fake_app.state = MagicMock(spec=[])  # no agent_graph attribute

    monkeypatch.setattr(app_main, "close_db_engine", AsyncMock())
    monkeypatch.setattr(app_main, "close_redis_pool", AsyncMock())

    await app_main._shutdown_resources(fake_app, logger)


# ---------------------------------------------------------------------------
# Health & Readiness endpoints
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_health_returns_ok(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert resp.headers.get("X-Trace-ID")


@pytest.mark.anyio
async def test_readiness_unready_when_db_fails(client, monkeypatch):
    """Force the readiness check's DB ping to fail."""
    from app.api import dependencies as deps

    fake_engine = MagicMock()

    class _FailingConn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, *a, **kw):
            raise RuntimeError("db ping failed")

    fake_engine.connect = MagicMock(return_value=_FailingConn())
    monkeypatch.setattr(deps, "db_engine", fake_engine, raising=False)

    resp = await client.get("/readiness")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unready"
    assert body["reason"] == "db"


@pytest.mark.anyio
async def test_readiness_unready_when_redis_fails(client, monkeypatch):
    from app.api import dependencies as deps

    # No DB engine -> skip DB check
    monkeypatch.setattr(deps, "db_engine", None, raising=False)
    # Pool present but ping will fail
    monkeypatch.setattr(deps, "redis_pool", MagicMock(), raising=False)

    fake_redis_client = MagicMock()
    fake_redis_client.ping = AsyncMock(side_effect=RuntimeError("redis down"))

    with patch("redis.asyncio.Redis", return_value=fake_redis_client):
        resp = await client.get("/readiness")

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unready"
    assert body["reason"] == "redis"


@pytest.mark.anyio
async def test_readiness_ready_when_no_deps_initialized(client, monkeypatch):
    """If neither DB nor Redis was initialized, readiness must short-circuit to ready."""
    from app.api import dependencies as deps

    monkeypatch.setattr(deps, "db_engine", None, raising=False)
    monkeypatch.setattr(deps, "redis_pool", None, raising=False)

    resp = await client.get("/readiness")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


# ---------------------------------------------------------------------------
# Global exception handler
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_global_exception_handler_returns_structured_500():
    """Mount a route that raises and verify the global handler converts to 500 JSON.

    Uses a fresh httpx client with raise_app_exceptions=False so the ASGI
    transport does not re-raise the test exception before the FastAPI handler
    can format it.
    """
    from httpx import ASGITransport, AsyncClient

    from app.main import app as fastapi_app

    @fastapi_app.get("/__test_boom_iter2")
    async def _boom():
        raise RuntimeError("kaboom")

    try:
        transport = ASGITransport(app=fastapi_app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/__test_boom_iter2")
    finally:
        fastapi_app.router.routes = [
            r for r in fastapi_app.router.routes
            if getattr(r, "path", None) != "/__test_boom_iter2"
        ]

    assert resp.status_code == 500
    body = resp.json()
    assert body["errorCode"] == "INTERNAL_SERVER_ERROR"
    assert "detail" in body
    assert "traceId" in body
    # Internal exception text MUST NOT be exposed.
    assert "kaboom" not in resp.text
