"""Tests for the crawler-facing per-result OG/SSR meta route (P1 Virality §A).

Covers:
- Found result  -> 200 with per-result OG/Twitter tags + redirect.
- Missing result -> 200 with GENERIC tags (never a 404/500 to a crawler).
- Service error  -> 200 generic (fail-safe, no 500).
- HTML escaping of hostile stored values.
- Absolute og:image resolution from a relative stored image_url.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.api import ShareableResultResponse
from app.services.database import ResultService

# Fixtures
from tests.fixtures.db_fixtures import override_db_dependency  # noqa: F401


@pytest.fixture
def mock_result_service():
    mock_svc = MagicMock(spec=ResultService)
    mock_svc.get_result_by_id = AsyncMock()
    from app.main import app as fastapi_app

    fastapi_app.dependency_overrides[ResultService] = lambda: mock_svc
    yield mock_svc
    fastapi_app.dependency_overrides.pop(ResultService, None)


@pytest.mark.anyio
@pytest.mark.usefixtures("override_db_dependency")
async def test_meta_found_returns_per_result_tags(async_client, mock_result_service):
    result_id = uuid.uuid4()
    mock_result_service.get_result_by_id.return_value = ShareableResultResponse(
        title="You are The Explorer",
        description="A bold, curious wanderer.",
        image_url="https://cdn.example.com/explorer.png",
    )

    resp = await async_client.get(f"/api/v1/result-meta/{result_id}")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    body = resp.text
    # Per-result OpenGraph + Twitter tags.
    assert 'property="og:title" content="You are The Explorer"' in body
    assert 'property="og:description" content="A bold, curious wanderer."' in body
    assert 'property="og:image" content="https://cdn.example.com/explorer.png"' in body
    assert 'name="twitter:card" content="summary_large_image"' in body
    assert 'name="twitter:title" content="You are The Explorer"' in body
    # Canonical + human redirect to the SPA result page.
    assert f"/result/{result_id}" in body
    assert 'http-equiv="refresh"' in body
    assert "location.replace(" in body


@pytest.mark.anyio
@pytest.mark.usefixtures("override_db_dependency")
async def test_meta_missing_returns_generic_not_500(async_client, mock_result_service):
    result_id = uuid.uuid4()
    mock_result_service.get_result_by_id.return_value = None

    resp = await async_client.get(f"/api/v1/result-meta/{result_id}")

    # Crawler must NEVER get a 404/500 — generic card with 200.
    assert resp.status_code == 200
    body = resp.text
    assert 'property="og:title" content="quafel"' in body
    assert 'property="og:description" content="Engaging AI-powered quizzes."' in body
    # Default og-image path resolved to an absolute URL.
    assert "/og-image.png" in body
    # Still redirects the human to the SPA result page.
    assert f"/result/{result_id}" in body


@pytest.mark.anyio
@pytest.mark.usefixtures("override_db_dependency")
async def test_meta_service_error_fails_safe(async_client, mock_result_service):
    result_id = uuid.uuid4()
    mock_result_service.get_result_by_id.side_effect = RuntimeError("db down")

    resp = await async_client.get(f"/api/v1/result-meta/{result_id}")

    assert resp.status_code == 200
    assert 'property="og:title" content="quafel"' in resp.text


@pytest.mark.anyio
@pytest.mark.usefixtures("override_db_dependency")
async def test_meta_escapes_hostile_values(async_client, mock_result_service):
    result_id = uuid.uuid4()
    mock_result_service.get_result_by_id.return_value = ShareableResultResponse(
        title='"><script>alert(1)</script>',
        description='</title><img src=x onerror=alert(1)>',
        image_url=None,
    )

    resp = await async_client.get(f"/api/v1/result-meta/{result_id}")

    assert resp.status_code == 200
    body = resp.text
    # Raw markup must not survive — it is HTML-escaped.
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body or "&lt;/title&gt;" in body


@pytest.mark.anyio
@pytest.mark.usefixtures("override_db_dependency")
async def test_meta_relative_image_made_absolute(async_client, mock_result_service):
    result_id = uuid.uuid4()
    mock_result_service.get_result_by_id.return_value = ShareableResultResponse(
        title="Pic",
        description="desc",
        image_url="/api/v1/media/abc123",
    )

    resp = await async_client.get(f"/api/v1/result-meta/{result_id}")

    assert resp.status_code == 200
    body = resp.text
    # The og:image must be absolute (start with http) — crawlers require it.
    assert 'property="og:image" content="http' in body
    assert "/api/v1/media/abc123" in body


@pytest.mark.anyio
@pytest.mark.usefixtures("override_db_dependency")
async def test_meta_invalid_uuid_422(async_client):
    resp = await async_client.get("/api/v1/result-meta/not-a-uuid")
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Deep-review #24 — canonical/og:url must come from the configured PUBLIC_SITE_URL
# (or a safe default in prod), NEVER from a spoofable, cache-reflected Host header.
# ---------------------------------------------------------------------------
@pytest.mark.anyio
@pytest.mark.usefixtures("override_db_dependency")
async def test_meta_uses_public_site_url_not_spoofed_host(
    async_client, mock_result_service, monkeypatch
):
    """With PUBLIC_SITE_URL configured, canonical/og:url use IT even when a hostile
    Host header is sent — the cached share card can't be poisoned via Host."""
    monkeypatch.setenv("PUBLIC_SITE_URL", "https://quafel.com")
    result_id = uuid.uuid4()
    mock_result_service.get_result_by_id.return_value = ShareableResultResponse(
        title="You are The Explorer",
        description="A bold, curious wanderer.",
        image_url="/og.png",  # relative -> resolved against the trusted base
    )

    resp = await async_client.get(
        f"/api/v1/result-meta/{result_id}",
        headers={"Host": "evil.attacker.example"},
    )

    assert resp.status_code == 200
    body = resp.text
    # Canonical + og:url are built from the trusted PUBLIC_SITE_URL.
    assert f'href="https://quafel.com/result/{result_id}"' in body
    assert f'property="og:url" content="https://quafel.com/result/{result_id}"' in body
    # The relative image resolved against the trusted base, not the spoofed Host.
    assert 'property="og:image" content="https://quafel.com/og.png"' in body
    # The spoofed Host must appear NOWHERE in the response body.
    assert "evil.attacker.example" not in body
    # Host did not influence the (trusted) body -> no Vary: Host needed.
    assert "host" not in {k.lower() for k in resp.headers.get("vary", "").split(", ")}


@pytest.mark.anyio
@pytest.mark.usefixtures("override_db_dependency")
async def test_meta_production_ignores_host_when_public_site_url_unset(
    async_client, mock_result_service, monkeypatch
):
    """In production with PUBLIC_SITE_URL unset, a spoofed Host is NOT reflected
    into the cached canonical/og:url — a generic default origin is used instead."""
    monkeypatch.delenv("PUBLIC_SITE_URL", raising=False)
    # Force the endpoint to see a production environment.
    from app.api.endpoints import results as results_mod

    monkeypatch.setattr(
        results_mod, "is_production", lambda _env: True, raising=False
    )

    result_id = uuid.uuid4()
    mock_result_service.get_result_by_id.return_value = None  # generic card

    resp = await async_client.get(
        f"/api/v1/result-meta/{result_id}",
        headers={"Host": "evil.attacker.example"},
    )

    assert resp.status_code == 200
    body = resp.text
    assert "evil.attacker.example" not in body
    # Falls back to the generic default site origin, not the Host.
    assert f"{results_mod._DEFAULT_SITE_URL}/result/{result_id}" in body


@pytest.mark.anyio
@pytest.mark.usefixtures("override_db_dependency")
async def test_meta_nonprod_uses_host_with_vary_header(
    async_client, mock_result_service, monkeypatch
):
    """In non-prod with no PUBLIC_SITE_URL, the Host-derived origin is allowed for
    dev ergonomics, but the response MUST carry Vary: Host so caches don't
    cross-serve a Host-specific body."""
    monkeypatch.delenv("PUBLIC_SITE_URL", raising=False)
    from app.api.endpoints import results as results_mod

    monkeypatch.setattr(results_mod, "is_production", lambda _env: False, raising=False)

    result_id = uuid.uuid4()
    mock_result_service.get_result_by_id.return_value = None

    resp = await async_client.get(f"/api/v1/result-meta/{result_id}")
    assert resp.status_code == 200
    # Host influenced the body -> Vary: Host is present.
    assert resp.headers.get("vary", "").lower() == "host"
