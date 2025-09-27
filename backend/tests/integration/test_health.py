# backend/tests/integration/test_health.py

from uuid import UUID

import pytest


@pytest.mark.integration
async def test_health_returns_ok_200(client):
    # /health is defined at the app root (no API_PREFIX)
    resp = await client.get("/health")

    # Basic status check
    assert resp.status_code == 200

    # JSON payload shape
    data = resp.json()
    assert isinstance(data, dict)
    assert (data.get("status") or "").lower() in {"ok", "healthy"}

    # Middleware adds a unique request trace id header
    trace_id = resp.headers.get("X-Trace-ID")
    assert trace_id, "X-Trace-ID header missing"
    # Validate it looks like a UUID
    UUID(trace_id)

    # Content-Type should be JSON
    content_type = resp.headers.get("content-type") or ""
    assert "application/json" in content_type.lower()
