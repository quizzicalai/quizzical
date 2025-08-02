"""
Pytest Configuration and Fixtures

This file defines shared fixtures that are available to all tests in the suite.
Fixtures are a powerful feature of pytest for managing the setup and teardown
of resources needed for testing, such as database connections or API clients.
"""

import asyncio
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.api.dependencies import get_db_session
from app.main import app
from app.models.db import Base

# --- Database Fixtures ---

# Use an in-memory SQLite database for fast, isolated tests.
# aiosqlite is the async driver for SQLite.
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

# Create a separate, async engine specifically for the test database.
test_engine = create_async_engine(TEST_DATABASE_URL)

# Create a session factory for the test database. This will be used to
# create new, clean sessions for each test.
TestingSessionLocal = async_sessionmaker(
    autocommit=False, autoflush=False, bind=test_engine
)


@pytest_asyncio.fixture(scope="session")
async def setup_database():
    """
    A session-scoped fixture to create the test database schema once for the
    entire test run. This is more efficient than creating the schema for each test.
    """
    async with test_engine.begin() as conn:
        # Create all tables defined in the ORM models.
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        # Drop all tables after the test session is complete.
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture(scope="function")
async def db_session(setup_database) -> AsyncGenerator[AsyncSession, None]:
    """
    A function-scoped fixture that provides a clean database session for each
    individual test function. It creates a transaction that is rolled back
    at the end of the test, ensuring perfect test isolation.
    """
    # Establish a connection and begin a transaction.
    connection = await test_engine.connect()
    transaction = await connection.begin()
    session = TestingSessionLocal(bind=connection)

    yield session

    # Rollback the transaction to undo any changes made during the test.
    await session.close()
    await transaction.rollback()
    await connection.close()


# --- API Test Client Fixture ---


@pytest_asyncio.fixture(scope="function")
async def test_client(
    db_session: AsyncSession,
) -> AsyncGenerator[AsyncClient, None]:
    """
    Provides an HTTPX AsyncClient for making requests to the FastAPI app.
    This client is configured to use the isolated test database by overriding
    the production `get_db_session` dependency.
    """

    async def override_get_db_session() -> AsyncGenerator[AsyncSession, None]:
        """
        A dependency override that replaces the production `get_db_session`
        with one that yields our isolated test session for the duration of a request.
        """
        yield db_session

    # Apply the dependency override to the FastAPI app.
    app.dependency_overrides[get_db_session] = override_get_db_session

    # Create the test client, which routes requests to our in-memory app.
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client

    # Clean up the dependency override after the test is complete.
    del app.dependency_overrides[get_db_session]


# --- Event Loop Fixture ---


@pytest.fixture(scope="session")
def event_loop():
    """
    Creates a new asyncio event loop for the entire test session.
    This is a best practice for `pytest-asyncio` when using session-scoped
    async fixtures.
    """
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()
