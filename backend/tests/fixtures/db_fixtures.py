# backend/tests/fixtures/db_fixtures.py
"""
Database fixtures for QuizzicalAI tests.

Two tracks:

1) Null DB (cache-only MVP)
   - override_db_dependency_null: FastAPI get_db_session -> None (global override per test)

2) SQLite in-memory AsyncSession
   - sqlite_db_session: isolated AsyncSession per test using nested SAVEPOINTs
   - override_db_dependency_sqlite: get_db_session -> sqlite_db_session for that test
   - sqlite_engine: optional engine exposure

Back-compat aliases:
   - db_session -> sqlite_db_session
   - override_db_dependency -> override_db_dependency_sqlite
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, Optional

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# --------------------------------------------------------------------------------------
# Ensure `backend/` is importable BEFORE importing app modules
# --------------------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_BACKEND_DIR = _THIS_FILE.parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# Lazy imports from the app (after sys.path fix)
from app.main import app as fastapi_app  # type: ignore
from app.api.dependencies import get_db_session  # type: ignore
from app.models.db import Base  # type: ignore

# ======================================================================================
# Track 1: Null DB (cache-only MVP)
# ======================================================================================

@pytest.fixture(scope="function")
def override_db_dependency_null():
    """
    Override FastAPI's `get_db_session` to yield None for this test.
    """
    async def _dep() -> AsyncGenerator[Optional[AsyncSession], None]:
        yield None

    fastapi_app.dependency_overrides[get_db_session] = _dep
    try:
        yield
    finally:
        fastapi_app.dependency_overrides.pop(get_db_session, None)


# ======================================================================================
# Track 2: SQLite in-memory AsyncSession (per-test isolation with nested SAVEPOINTs)
# ======================================================================================

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

# Module-level engine + session factory.
# We bind the *connection* at runtime so all work stays on a single connection.
_sql_engine: AsyncEngine = create_async_engine(TEST_DATABASE_URL, echo=False)
_SessionLocal = async_sessionmaker(class_=AsyncSession, expire_on_commit=False)


@asynccontextmanager
async def _session_ctx() -> AsyncGenerator[AsyncSession, None]:
    """
    Single-connection session with:
      - create_all() on the SAME connection (DDL committed before tests)
      - Per-test root transaction on the connection
      - Nested SAVEPOINT so app-level session.commit() won't break isolation
      - SQLAlchemy 2.x–safe restart of the nested SAVEPOINT (no .connection.invalidated)
    """
    conn: AsyncConnection = await _sql_engine.connect()

    # ---- Schema setup on this connection
    created = False
    try:
        await conn.run_sync(Base.metadata.create_all)
        await conn.commit()  # finish implicit DDL txn
        created = True
    except Exception:
        # Make sure the connection is left clean if DDL fails
        try:
            await conn.rollback()
        except Exception:
            pass
        created = False

    # ---- Start per-test root transaction on the connection
    root_tx = await conn.begin()

    # ---- Bind an AsyncSession to THIS connection
    session: AsyncSession = _SessionLocal(bind=conn)

    # Create a nested SAVEPOINT so app code can commit safely
    await session.begin_nested()

    # SQLAlchemy-recommended recipe: re-open a SAVEPOINT when the previous one ends.
    # IMPORTANT: listen on the *sync* session behind AsyncSession.
    def _restart_savepoint(sess, trans):
        # Use parent-based check; don't touch trans.connection (method in SA 2.x)
        parent = getattr(trans, "_parent", None)
        if trans.nested and (parent is None or not getattr(parent, "nested", False)):
            # Re-open nested SAVEPOINT after a commit/rollback inside the test
            sess.begin_nested()

    event.listen(session.sync_session, "after_transaction_end", _restart_savepoint)

    try:
        yield session
    finally:
        # Remove listener first to avoid callbacks during shutdown
        event.remove(session.sync_session, "after_transaction_end", _restart_savepoint)

        # Close the session (releases SAVEPOINT if still open)
        try:
            await session.close()
        finally:
            # Roll back the root transaction to discard changes
            try:
                await root_tx.rollback()
            finally:
                # Optional: drop schema for absolute cleanliness on this connection
                if created:
                    try:
                        await conn.run_sync(Base.metadata.drop_all)
                        await conn.commit()
                    except Exception:
                        # If drop/commit fails, ensure the connection is clean
                        try:
                            await conn.rollback()
                        except Exception:
                            pass
                await conn.close()


@pytest_asyncio.fixture(scope="function")
async def sqlite_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    A clean AsyncSession per test function using an in-memory SQLite DB bound to a single connection.
    """
    async with _session_ctx() as s:
        yield s


@pytest_asyncio.fixture(scope="function")
async def override_db_dependency_sqlite(sqlite_db_session: AsyncSession):
    """
    Override FastAPI's `get_db_session` to yield `sqlite_db_session` for this test.
    """
    async def _dep() -> AsyncGenerator[AsyncSession, None]:
        yield sqlite_db_session

    fastapi_app.dependency_overrides[get_db_session] = _dep
    try:
        yield
    finally:
        fastapi_app.dependency_overrides.pop(get_db_session, None)


# --------------------------------------------------------------------------------------
# Back-compat aliases
# --------------------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="function")
async def db_session(sqlite_db_session: AsyncSession):
    """Alias: `db_session` -> `sqlite_db_session`."""
    yield sqlite_db_session


@pytest_asyncio.fixture(scope="function")
async def override_db_dependency(override_db_dependency_sqlite):
    """Alias: `override_db_dependency` -> `override_db_dependency_sqlite`."""
    yield  # delegated


# --------------------------------------------------------------------------------------
# Optional: expose engine if a test needs engine-level operations
# --------------------------------------------------------------------------------------

@pytest.fixture(scope="session")
def sqlite_engine() -> AsyncEngine:
    return _sql_engine

# --------------------------------------------------------------------------------------
# Fakes for unit tests that don't need a real DB
# --------------------------------------------------------------------------------------

class FakeResult:
    """
    Minimal result object supporting .mappings().all() and .scalars().first()
    """
    def __init__(self, mappings_rows=None, scalar_obj=None):
        self._mappings_rows = list(mappings_rows or [])
        self._scalar_obj = scalar_obj

    class _Mappings:
        def __init__(self, rows):
            self._rows = rows
        def all(self):
            # Return dict-like rows so callers can .get("field")
            return list(self._rows)

    class _Scalars:
        def __init__(self, obj):
            self._obj = obj
        def first(self):
            return self._obj

    def mappings(self):
        return FakeResult._Mappings(self._mappings_rows)

    def scalars(self):
        return FakeResult._Scalars(self._scalar_obj)


class FakeAsyncSession:
    """
    AsyncSession-like stub supporting:
      - async context manager: `async with FakeAsyncSession(...) as db: ...`
      - execute(): returns the provided FakeResult
      - close(): no-op (tracked for completeness)
    You can monkeypatch `.execute` per-test to raise, etc.
    """
    def __init__(self, result: FakeResult):
        self._result = result
        self._closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()

    async def execute(self, *_a, **_k):
        return self._result

    async def close(self):
        self._closed = True