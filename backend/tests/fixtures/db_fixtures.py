# backend/tests/fixtures/db_fixtures.py
"""
Database fixtures for QuizzicalAI tests.

Provides a shared SQLite in-memory database engine and session factory.
Ensures schema creation happens on the active connection to avoid
isolation issues with aiosqlite :memory: databases.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import event, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

# --------------------------------------------------------------------------------------
# Ensure `backend/` is importable BEFORE importing app modules
# --------------------------------------------------------------------------------------
_THIS_FILE = Path(__file__).resolve()
_BACKEND_DIR = _THIS_FILE.parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.main import app as fastapi_app
from app.api.dependencies import get_db_session
from app.models.db import Base

# ======================================================================================
# SQLite Compatibility Layer
# ======================================================================================

@compiles(PGUUID, "sqlite")
def compile_pg_uuid_as_text(type_, compiler, **kw):
    return "TEXT"

@compiles(JSONB, "sqlite")
def compile_jsonb_as_json(type_, compiler, **kw):
    return "JSON"

try:
    from pgvector.sqlalchemy import Vector
    @compiles(Vector, "sqlite")
    def compile_vector_as_text(type_, compiler, **kw):
        return "TEXT"
except ImportError:
    pass


# ======================================================================================
# Engine Configuration
# ======================================================================================

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

_test_engine = create_async_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool, 
)

_TestSessionLocal = async_sessionmaker(
    bind=_test_engine,
    class_=AsyncSession,
    expire_on_commit=False
)

# Intercept and sanitize SQL before it hits SQLite to handle Postgres-specifics
@event.listens_for(_test_engine.sync_engine, "before_cursor_execute", retval=True)
def fix_postgres_syntax_for_sqlite(conn, cursor, statement, parameters, context, executemany):
    if "::jsonb" in statement:
        statement = statement.replace("::jsonb", "")
    return statement, parameters


# ======================================================================================
# Session Fixture
# ======================================================================================

@pytest_asyncio.fixture(scope="function")
async def sqlite_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Yields an AsyncSession for the test with Foreign Keys enabled.
    """
    # 1. Acquire connection
    connection = await _test_engine.connect()
    
    # [FIX] Explicitly enable foreign keys on THIS connection.
    # 'execute' starts an implicit transaction. We must 'commit' it immediately
    # to apply the PRAGMA to the connection state and close the implicit transaction.
    # This allows the subsequent 'connection.begin()' to start a clean transaction.
    await connection.execute(text("PRAGMA foreign_keys=ON"))
    await connection.commit()
    
    # 2. Ensure schema exists on this specific connection
    # We start an explicit transaction for DDL
    async with connection.begin():
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)

    # 3. Start transaction for the test itself
    transaction = await connection.begin()
    
    # 4. Bind session to this connection
    session = _TestSessionLocal(bind=connection)
    
    # 5. Nested transaction for app savepoints
    await session.begin_nested()

    @event.listens_for(session.sync_session, "after_transaction_end")
    def restart_savepoint(session, transaction):
        if transaction.nested and not transaction._parent.nested:
            session.begin_nested()

    try:
        yield session
    finally:
        await session.close()
        if transaction.is_active:
            await transaction.rollback()
        await connection.close()


@pytest_asyncio.fixture(scope="function")
async def override_db_dependency(sqlite_db_session: AsyncSession):
    """
    Override FastAPI's `get_db_session` to yield the test's isolated session.
    """
    async def _dep() -> AsyncGenerator[AsyncSession, None]:
        yield sqlite_db_session

    fastapi_app.dependency_overrides[get_db_session] = _dep
    try:
        yield
    finally:
        fastapi_app.dependency_overrides.pop(get_db_session, None)