# tests/fixtures/http_client.py
"""
Lifespan-aware HTTP client fixtures without nested fixture activation.

- async_client: httpx.AsyncClient bound to the FastAPI app; ensures lifespan runs
  across httpx versions.
- client: back-compat alias that *declares* Redis/DB overrides as dependencies so
  they are active before app startup, without calling request.getfixturevalue().
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path
from typing import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Ensure `backend/` is importable
_THIS_FILE = Path(__file__).resolve()
_BACKEND_DIR = _THIS_FILE.parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.main import app as fastapi_app  # type: ignore


@pytest_asyncio.fixture(scope="function")
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    """
    httpx AsyncClient with proper app startup/shutdown around each test,
    compatible with multiple httpx versions.
    """
    params = inspect.signature(ASGITransport.__init__).parameters

    if "lifespan" in params:
        # httpx >= 0.27 supports built-in lifespan management via transport
        transport = ASGITransport(app=fastapi_app, lifespan="auto")
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    else:
        # Older httpx: manually run FastAPI's lifespan context
        async with fastapi_app.router.lifespan_context(fastapi_app):
            transport = ASGITransport(app=fastapi_app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                yield client


@pytest_asyncio.fixture(scope="function")
async def client(
    async_client,
    # Declare overrides explicitly so pytest sets them up BEFORE async_client
    # (no manual activation or nested event loop shenanigans).
    override_redis_dep,          # from tests/fixtures/redis_fixtures.py
    override_db_dependency,      # alias to sqlite override in your db_fixtures
):
    """Back-compat alias to `async_client` with common overrides enabled."""
    yield async_client
