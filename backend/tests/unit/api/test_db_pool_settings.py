"""AC-DB-PERF-1..3 — DB pool sizing is configurable.

Phase 7 (performance): hardcoded pool_size=10/max_overflow=5 cannot scale
beyond ~15 concurrent quizzes. Operators must be able to tune via config.
"""
from __future__ import annotations

import pytest
from unittest.mock import patch

from app.api import dependencies as deps


@pytest.fixture(autouse=True)
def _reset_globals():
    saved_engine = deps.db_engine
    saved_factory = deps.async_session_factory
    deps.db_engine = None
    deps.async_session_factory = None
    yield
    deps.db_engine = saved_engine
    deps.async_session_factory = saved_factory


def test_pool_size_defaults_when_settings_absent(monkeypatch):
    """AC-DB-PERF-1: when settings.database is missing or has no overrides,
    we still pass sane defaults (pool_size>=10, max_overflow>=5) to PostgreSQL."""
    captured: dict = {}

    def fake_engine(url, **kwargs):  # type: ignore[no-untyped-def]
        captured["url"] = url
        captured.update(kwargs)
        # return a sentinel; the factory call that follows won't be exercised
        # because we'll raise before .async_sessionmaker.
        raise RuntimeError("stop here")

    monkeypatch.setattr(deps, "create_async_engine", fake_engine)

    with pytest.raises(RuntimeError, match="stop here"):
        deps.create_db_engine_and_session_maker(
            "postgresql+asyncpg://user:pw@localhost/db"
        )

    assert captured.get("pool_size", 0) >= 10, (
        f"default pool_size must be >= 10, got {captured.get('pool_size')}"
    )
    assert captured.get("max_overflow", 0) >= 5, (
        f"default max_overflow must be >= 5, got {captured.get('max_overflow')}"
    )


def test_pool_size_honours_settings_database_pool_size(monkeypatch):
    """AC-DB-PERF-2: settings.database.pool_size overrides the default."""
    from app.core.config import settings

    captured: dict = {}

    class FakeDB:
        pool_size = 40
        max_overflow = 20
        pool_recycle_s = 1800

    def fake_engine(url, **kwargs):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        raise RuntimeError("stop")

    monkeypatch.setattr(deps, "create_async_engine", fake_engine)
    monkeypatch.setattr(settings, "database", FakeDB(), raising=False)

    with pytest.raises(RuntimeError, match="stop"):
        deps.create_db_engine_and_session_maker(
            "postgresql+asyncpg://user:pw@localhost/db"
        )

    assert captured.get("pool_size") == 40
    assert captured.get("max_overflow") == 20


def test_sqlite_url_does_not_receive_pool_size(monkeypatch):
    """AC-DB-PERF-3: SQLite URLs must NOT receive pool_size/max_overflow
    (StaticPool ignores them and SQLAlchemy raises)."""
    captured: dict = {}

    def fake_engine(url, **kwargs):  # type: ignore[no-untyped-def]
        captured["url"] = url
        captured.update(kwargs)
        raise RuntimeError("stop")

    monkeypatch.setattr(deps, "create_async_engine", fake_engine)

    with pytest.raises(RuntimeError, match="stop"):
        deps.create_db_engine_and_session_maker("sqlite+aiosqlite:///:memory:")

    assert "pool_size" not in captured
    assert "max_overflow" not in captured
    assert captured.get("poolclass") is not None
