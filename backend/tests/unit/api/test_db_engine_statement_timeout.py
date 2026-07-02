"""P10 (2026-07-02) — server-side PostgreSQL ``statement_timeout``.

The engine factory must pass a driver-appropriate ``connect_args`` so every
pooled connection gets a ~15s statement timeout:

- ``postgresql+psycopg://`` (psycopg v3 — what the production KV
  ``database-url`` uses) → libpq startup ``options``.
- ``postgresql+asyncpg://`` (possible local/dev URLs) → ``server_settings``
  (asyncpg has no libpq ``options`` passthrough).
- SQLite (tests) → untouched.
- ``settings.database.statement_timeout_ms`` configures the value; ``0``
  disables entirely.

No real DB connection is made — ``create_async_engine`` is stubbed and the
captured kwargs are asserted.
"""

from __future__ import annotations

import pytest

from app.api import dependencies as deps
from app.api.dependencies import _pg_statement_timeout_connect_args
from app.core.config import settings

# ---------------------------------------------------------------------------
# Helper-level: driver branching
# ---------------------------------------------------------------------------


def test_psycopg_url_uses_libpq_options():
    args = _pg_statement_timeout_connect_args(
        "postgresql+psycopg://u:p@host:5432/db", 15000
    )
    assert args == {"options": "-c statement_timeout=15000"}


def test_asyncpg_url_uses_server_settings():
    args = _pg_statement_timeout_connect_args(
        "postgresql+asyncpg://u:p@host:5432/db", 15000
    )
    assert args == {"server_settings": {"statement_timeout": "15000"}}


def test_bare_postgresql_scheme_defaults_to_libpq_options():
    args = _pg_statement_timeout_connect_args("postgresql://u:p@host/db", 9000)
    assert args == {"options": "-c statement_timeout=9000"}


def test_zero_disables():
    assert _pg_statement_timeout_connect_args("postgresql+psycopg://h/db", 0) == {}


def test_negative_disables():
    assert _pg_statement_timeout_connect_args("postgresql+psycopg://h/db", -1) == {}


def test_non_postgres_scheme_untouched():
    assert _pg_statement_timeout_connect_args("sqlite+aiosqlite:///:memory:", 15000) == {}
    assert _pg_statement_timeout_connect_args("mysql+aiomysql://h/db", 15000) == {}


# ---------------------------------------------------------------------------
# Factory-level: create_db_engine_and_session_maker passes connect_args
# ---------------------------------------------------------------------------


@pytest.fixture()
def capture_engine_kwargs(monkeypatch):
    """Stub create_async_engine / async_sessionmaker and reset the idempotency
    globals so each test exercises a fresh factory call."""
    captured: dict = {}

    def _fake_create_engine(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return object()

    class _FakeSessionMaker:
        def __init__(self, **_kwargs):
            pass

    monkeypatch.setattr(deps, "create_async_engine", _fake_create_engine)
    monkeypatch.setattr(deps, "async_sessionmaker", _FakeSessionMaker)
    monkeypatch.setattr(deps, "db_engine", None)
    monkeypatch.setattr(deps, "async_session_factory", None)
    yield captured
    # Never leak the stub engine into other tests.
    monkeypatch.setattr(deps, "db_engine", None)
    monkeypatch.setattr(deps, "async_session_factory", None)


def test_factory_psycopg_gets_statement_timeout(capture_engine_kwargs):
    deps.create_db_engine_and_session_maker("postgresql+psycopg://u:p@h:5432/db")
    kwargs = capture_engine_kwargs["kwargs"]
    assert kwargs["connect_args"] == {"options": "-c statement_timeout=15000"}
    # Pool sizing still applied on the non-sqlite branch.
    assert "pool_size" in kwargs and "pool_timeout" in kwargs


def test_factory_asyncpg_gets_server_settings(capture_engine_kwargs):
    deps.create_db_engine_and_session_maker("postgresql+asyncpg://u:p@h:5432/db")
    kwargs = capture_engine_kwargs["kwargs"]
    assert kwargs["connect_args"] == {
        "server_settings": {"statement_timeout": "15000"}
    }


def test_factory_sqlite_branch_untouched(capture_engine_kwargs):
    deps.create_db_engine_and_session_maker("sqlite+aiosqlite:///:memory:")
    kwargs = capture_engine_kwargs["kwargs"]
    # SQLite keeps its own connect_args; no statement_timeout is injected.
    assert kwargs["connect_args"] == {"check_same_thread": False}
    assert "pool_size" not in kwargs


def test_factory_honours_configured_value(capture_engine_kwargs, monkeypatch):
    monkeypatch.setattr(settings.database, "statement_timeout_ms", 250, raising=False)
    deps.create_db_engine_and_session_maker("postgresql+psycopg://u:p@h/db")
    kwargs = capture_engine_kwargs["kwargs"]
    assert kwargs["connect_args"] == {"options": "-c statement_timeout=250"}


def test_factory_zero_disables_timeout(capture_engine_kwargs, monkeypatch):
    monkeypatch.setattr(settings.database, "statement_timeout_ms", 0, raising=False)
    deps.create_db_engine_and_session_maker("postgresql+psycopg://u:p@h/db")
    kwargs = capture_engine_kwargs["kwargs"]
    assert "connect_args" not in kwargs
