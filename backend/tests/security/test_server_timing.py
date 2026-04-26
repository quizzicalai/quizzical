"""Server-Timing header is emitted on every response and exposed via CORS."""
from __future__ import annotations

import re

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app


@pytest.mark.asyncio
async def test_server_timing_header_present_on_health() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        r = await ac.get("/health")
    assert r.status_code == 200
    st = r.headers.get("Server-Timing")
    assert st is not None
    # Format: "app;dur=<number>"
    assert re.match(r"^app;dur=\d+(\.\d+)?$", st), st


@pytest.mark.asyncio
async def test_server_timing_exposed_via_cors() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        r = await ac.get("/health", headers={"Origin": "http://localhost:3000"})
    expose = r.headers.get("access-control-expose-headers", "")
    assert "Server-Timing" in expose
