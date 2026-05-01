"""§21 Phase 5 — `GET /api/media/{asset_id}` for the local provider.

ACs covered:
- `AC-PRECOMP-IMG-3` / `AC-PRECOMP-PERF-4` — immutable cache + strong ETag.
- `AC-PRECOMP-IMG-3` — `If-None-Match` returns `304 Not Modified`.
- 404 when asset missing or has no `bytes_blob` (provider=fal).
"""

from __future__ import annotations

import hashlib
import uuid

import pytest

from app.main import API_PREFIX
from app.models.db import MediaAsset

API = API_PREFIX.rstrip("/")


async def _insert_asset(
    sqlite_db_session,
    *,
    data: bytes,
    provider: str = "local",
    content_type: str | None = None,
) -> MediaAsset:
    asset = MediaAsset(
        id=uuid.uuid4(),
        content_hash=hashlib.sha256(data).hexdigest(),
        prompt_hash="ph-" + hashlib.sha256(data).hexdigest()[:16],
        storage_provider=provider,
        storage_uri=f"/api/media/{uuid.uuid4()}",
        bytes_blob=data if provider == "local" else None,
        prompt_payload={"content_type": content_type} if content_type else {},
    )
    sqlite_db_session.add(asset)
    await sqlite_db_session.commit()
    await sqlite_db_session.refresh(asset)
    return asset


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_immutable_cache_headers_present(async_client, sqlite_db_session):
    asset = await _insert_asset(sqlite_db_session, data=b"hello-bytes-png")

    resp = await async_client.get(f"{API}/media/{asset.id}")
    assert resp.status_code == 200
    cc = resp.headers.get("cache-control") or resp.headers.get("Cache-Control")
    assert cc is not None
    assert "immutable" in cc
    assert "max-age=31536000" in cc
    assert "public" in cc
    assert resp.headers.get("x-content-type-options") == "nosniff"


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_etag_is_content_hash(async_client, sqlite_db_session):
    data = b"the-image-bytes"
    asset = await _insert_asset(sqlite_db_session, data=data)

    resp = await async_client.get(f"{API}/media/{asset.id}")
    assert resp.status_code == 200
    etag = resp.headers.get("etag") or resp.headers.get("ETag")
    expected = f'"{hashlib.sha256(data).hexdigest()}"'
    assert etag == expected
    assert resp.content == data


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_if_none_match_returns_304(async_client, sqlite_db_session):
    data = b"cached-png"
    asset = await _insert_asset(sqlite_db_session, data=data)
    etag = f'"{hashlib.sha256(data).hexdigest()}"'

    resp = await async_client.get(
        f"{API}/media/{asset.id}", headers={"If-None-Match": etag}
    )
    assert resp.status_code == 304
    assert resp.content == b""
    # ETag still echoed on 304 per RFC 7232.
    assert (resp.headers.get("etag") or resp.headers.get("ETag")) == etag


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_if_none_match_with_different_etag_returns_200(
    async_client, sqlite_db_session
):
    asset = await _insert_asset(sqlite_db_session, data=b"abcd")
    resp = await async_client.get(
        f"{API}/media/{asset.id}", headers={"If-None-Match": '"deadbeef"'}
    )
    assert resp.status_code == 200


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_missing_asset_returns_404(async_client):
    resp = await async_client.get(f"{API}/media/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_fal_provider_without_blob_returns_404(async_client, sqlite_db_session):
    """`provider=fal` rows have no `bytes_blob`; the local endpoint must
    not invent bytes for them — clients should follow the upstream URL."""
    asset = await _insert_asset(sqlite_db_session, data=b"ignored", provider="fal")
    # bytes_blob is None for fal rows
    asset.bytes_blob = None
    sqlite_db_session.add(asset)
    await sqlite_db_session.commit()
    resp = await async_client.get(f"{API}/media/{asset.id}")
    assert resp.status_code == 404


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_content_type_from_prompt_payload(async_client, sqlite_db_session):
    asset = await _insert_asset(
        sqlite_db_session, data=b"jpg-bytes", content_type="image/jpeg"
    )
    resp = await async_client.get(f"{API}/media/{asset.id}")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/jpeg")


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_default_content_type_png(async_client, sqlite_db_session):
    asset = await _insert_asset(sqlite_db_session, data=b"png-bytes")
    resp = await async_client.get(f"{API}/media/{asset.id}")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/png")
