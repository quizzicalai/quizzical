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
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
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

# Using shared cache for in-memory SQLite helps with connection isolation,
# though StaticPool is the primary mechanism.
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

# [CRITICAL FIX] Intercept and sanitize SQL before it hits SQLite.
# This removes PostgreSQL-specific casting (e.g., "DEFAULT '[]'::jsonb") which causes
# "unrecognized token: ':'" errors in SQLite.
@event.listens_for(_test_engine.sync_engine, "before_cursor_execute", retval=True)
def fix_postgres_syntax_for_sqlite(conn, cursor, statement, parameters, context, executemany):
    if "::jsonb" in statement:
        statement = statement.replace("::jsonb", "")
    return statement, parameters


# ======================================================================================
# Session Fixture (The Fix)
# ======================================================================================

@pytest_asyncio.fixture(scope="function")
async def sqlite_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Yields an AsyncSession for the test.
    
    CRITICAL CHANGE: We create the tables on THIS connection at the start of the test.
    This guarantees the tables exist for the duration of the session, avoiding
    "no such table" errors caused by aiosqlite connection isolation.
    """
    # 1. Acquire connection
    connection = await _test_engine.connect()
    
    # 2. Ensure schema exists on this specific connection
    # Begin a transaction for DDL
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