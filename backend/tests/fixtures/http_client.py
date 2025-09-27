"""
HTTP client + optional DB override fixtures.

What this provides:
- sqlite_engine / db_session: ephemeral in-memory SQLite AsyncSession using nested
  SAVEPOINTs so app-level commit() does not end test isolation.
- override_db_dependency: overrides FastAPI's `get_db_session` to yield our test session.
- async_client: httpx.AsyncClient bound to the FastAPI app with lifespan="on" so
  startup/shutdown run per test (with whatever patches you've applied elsewhere).

This file is intentionally DB-light. Today your app reads/writes from Redis only;
however, a number of modules already import `get_db_session`. Providing a no-op,
isolated session keeps the dependency graph happy and gives room to grow when
you add persistence.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Ensure `backend/` is importable
_THIS_FILE = Path(__file__).resolve()
_BACKEND_DIR = _THIS_FILE.parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.main import app as fastapi_app  # type: ignore
from app.api.dependencies import get_db_session  # type: ignore
from app.models.db import Base  # type: ignore


# ------------------------
# SQLite async session
# ------------------------

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
_sql_engine = create_async_engine(TEST_DATABASE_URL, future=True)
_SessionLocal = async_sessionmaker(bind=_sql_engine, expire_on_commit=False, autoflush=False, autocommit=False)


@asynccontextmanager
async def _session_ctx() -> AsyncGenerator[AsyncSession, None]:
    conn = await _sql_engine.connect()

    created = False
    try:
        # SQLite will no-op pgvector index bits; tolerate failures silently.
        await conn.run_sync(Base.metadata.create_all)
        created = True
    except Exception:
        created = False

    trans = await conn.begin()
    session = _SessionLocal(bind=conn)

    # Nested SAVEPOINT so app code can call commit() without breaking isolation
    await session.begin_nested()

    @event.listens_for(session.sync_session, "after_transaction_end")
    def _restart_nested(sess, trans_):
        if trans_.nested and not trans_.connection.invalidated:
            sess.begin_nested()

    try:
        yield session
    finally:
        await session.close()
        await trans.rollback()
        if created:
            try:
                await conn.run_sync(Base.metadata.drop_all)
            except Exception:
                pass
        await conn.close()


@pytest_asyncio.fixture(scope="function")
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    A clean AsyncSession per test function.
    """
    async with _session_ctx() as s:
        yield s


@pytest_asyncio.fixture(scope="function")
async def override_db_dependency(db_session: AsyncSession):
    """
    Override the FastAPI `get_db_session` dependency for the duration of a test.
    """
    async def _dep() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    fastapi_app.dependency_overrides[get_db_session] = _dep
    try:
        yield
    finally:
        fastapi_app.dependency_overrides.pop(get_db_session, None)


# ------------------------
# HTTP client
# ------------------------

@pytest_asyncio.fixture(scope="function")
async def async_client(override_db_dependency) -> AsyncGenerator[AsyncClient, None]:
    """
    httpx AsyncClient bound to the app with lifespan enabled.

    Combine with other overrides (e.g., `override_redis_dep`) in your tests or
    in a higher-level conftest.
    """
    transport = ASGITransport(app=fastapi_app, lifespan="on")
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client
