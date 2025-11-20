# backend/tests/integration/test_health.py

import uuid
import pytest

@pytest.mark.anyio
async def test_health_returns_ok_200(client):
    """
    Verifies the /health endpoint returns a 200 OK status,
    proper JSON structure, and the custom trace header.
    """
    # /health is defined at the app root (no API_PREFIX)
    resp = await client.get("/health")

    # 1. Basic status check
    assert resp.status_code == 200

    # 2. JSON payload shape
    data = resp.json()
    assert isinstance(data, dict)
    assert (data.get("status") or "").lower() in {"ok", "healthy"}

    # 3. Middleware adds a unique request trace id header
    trace_id = resp.headers.get("X-Trace-ID")
    assert trace_id, "X-Trace-ID header missing"
    # Validate it looks like a UUID
    uuid.UUID(trace_id)

    # 4. Content-Type should be JSON
    content_type = resp.headers.get("content-type") or ""
    assert "application/json" in content_type.lower()