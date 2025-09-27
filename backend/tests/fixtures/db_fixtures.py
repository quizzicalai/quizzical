"""
Database fixtures for QuizzicalAI tests.

This module offers two tracks:

1) Null DB (cache-only MVP)
   - `override_db_dependency_null`: globally override FastAPI dependency
     `get_db_session` to yield `None`. Use this when endpoints should not touch
     the database at all (today's default behavior).

2) SQLite in-memory AsyncSession
   - `sqlite_db_session`: an ephemeral, isolated AsyncSession per test using
     nested SAVEPOINTs so app-level `commit()` doesn't end isolation.
   - `override_db_dependency_sqlite`: override FastAPI `get_db_session` to yield
     the above session for the duration of a test.
   - (Optional) `sqlite_engine` if you need engine-level access.

Notes:
- We keep `backend/` on `sys.path` so `from app...` imports work in tests.
- The models include `pgvector.Vector`. When running `create_all()` under SQLite,
  we catch and tolerate DDL issues (tables may not be created). For tests that
  actually interact with the ORM you should either (a) skip vector columns or
  (b) switch to a Postgres test DB. For cache-only and dependency wiring tests,
  this is fine.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator, Optional

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Ensure `backend/` is importable
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
async def override_db_dependency_null():
    """
    Override FastAPI's `get_db_session` to yield None.

    Use this for tests where the DB must not be touched. Any code path that
    tries to use the session will naturally fail, which is desirable to keep
    tests honest about current behavior.
    """
    async def _dep() -> AsyncGenerator[Optional[AsyncSession], None]:
        yield None

    fastapi_app.dependency_overrides[get_db_session] = _dep
    try:
        yield
    finally:
        fastapi_app.dependency_overrides.pop(get_db_session, None)


# ======================================================================================
# Track 2: SQLite in-memory AsyncSession
# ======================================================================================

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
_sql_engine = create_async_engine(TEST_DATABASE_URL, future=True)
_SessionLocal = async_sessionmaker(
    bind=_sql_engine,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


@asynccontextmanager
async def _session_ctx() -> AsyncGenerator[AsyncSession, None]:
    """
    Create a single-connection session with:
      - Per-test top-level transaction
      - Nested SAVEPOINT (so app code can call `commit()` without ending isolation)
      - Best-effort create_all/drop_all on the same connection

    We catch DDL errors due to `pgvector` on SQLite and proceed (tables may not
    exist; that's acceptable for tests that aren't executing ORM queries).
    """
    conn = await _sql_engine.connect()

    created = False
    try:
        await conn.run_sync(Base.metadata.create_all)
        created = True
    except Exception:
        # pgvector and Postgres-only bits may fail under SQLite; ignore for tests that don't need tables
        created = False

    trans = await conn.begin()
    session = _SessionLocal(bind=conn)

    # Start nested SAVEPOINT so `session.commit()` inside app code doesn't end isolation
    await session.begin_nested()

    @event.listens_for(session.sync_session, "after_transaction_end")
    def _restart_savepoint(sess, trans_):
        # Re-open SAVEPOINT whenever the nested transaction ends
        if trans_.nested and not trans_.connection.invalidated:
            sess.begin_nested()

    try:
        yield session
    finally:
        # Teardown in reverse order
        await session.close()
        await trans.rollback()
        if created:
            try:
                await conn.run_sync(Base.metadata.drop_all)
            except Exception:
                pass
        await conn.close()


@pytest_asyncio.fixture(scope="function")
async def sqlite_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    A clean AsyncSession per test function using an in-memory SQLite DB.
    """
    async with _session_ctx() as s:
        yield s


@pytest_asyncio.fixture(scope="function")
async def override_db_dependency_sqlite(sqlite_db_session: AsyncSession):
    """
    Override FastAPI's `get_db_session` to yield the `sqlite_db_session` for this test.
    """
    async def _dep() -> AsyncGenerator[AsyncSession, None]:
        yield sqlite_db_session

    fastapi_app.dependency_overrides[get_db_session] = _dep
    try:
        yield
    finally:
        fastapi_app.dependency_overrides.pop(get_db_session, None)


# ======================================================================================
# Optional: expose engine if a test needs engine-level operations
# ======================================================================================

@pytest.fixture(scope="session")
def sqlite_engine():
    """
    Expose the module-level async engine (in-memory). Most tests should prefer
    `sqlite_db_session`, but this can be used for low-level introspection.
    """
    return _sql_engine
