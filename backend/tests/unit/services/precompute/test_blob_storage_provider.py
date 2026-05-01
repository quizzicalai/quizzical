"""§21 Phase 12 — Azure Blob storage provider unit tests."""

from __future__ import annotations

import uuid

import pytest

from app.models.db import MediaAsset
from app.services.precompute.storage import (
    AzureBlobConfigError,
    AzureBlobProvider,
    DualWriteResolver,
    FalProvider,
    LocalProvider,
    StorageProvider,
)

pytestmark = pytest.mark.anyio


def _asset(provider: str, *, uri: str = "", bytes_blob: bytes | None = None) -> MediaAsset:
    return MediaAsset(
        id=uuid.uuid4(),
        content_hash="h-" + uuid.uuid4().hex,
        prompt_hash="p-" + uuid.uuid4().hex,
        storage_provider=provider,
        storage_uri=uri,
        bytes_blob=bytes_blob,
        prompt_payload={},
    )


# ---------------------------------------------------------------------------
# Provider basics.
# ---------------------------------------------------------------------------


def test_all_providers_satisfy_protocol():
    assert isinstance(LocalProvider(), StorageProvider)
    assert isinstance(FalProvider(), StorageProvider)
    assert isinstance(
        AzureBlobProvider(base_url="https://x.blob", container="c"),
        StorageProvider,
    )


def test_blob_provider_requires_base_url_and_container():
    with pytest.raises(AzureBlobConfigError):
        AzureBlobProvider(base_url="", container="c")
    with pytest.raises(AzureBlobConfigError):
        AzureBlobProvider(base_url="https://x", container="")


def test_local_resolve_uses_api_path():
    a = _asset("local", uri="/api/v1/media/x")
    assert LocalProvider().resolve(a) == f"/api/v1/media/{a.id}"


def test_fal_resolve_passthrough():
    a = _asset("fal", uri="https://fal/img.png")
    assert FalProvider().resolve(a) == "https://fal/img.png"


# ---------------------------------------------------------------------------
# AzureBlobProvider.upload — exercised via injected mock SDK factory.
# ---------------------------------------------------------------------------


class _FakeBlobClient:
    def __init__(self, store: dict, key: tuple[str, str]):
        self._store = store
        self._key = key

    async def upload_blob(self, data, overwrite: bool, content_settings=None):
        if self._key in self._store and not overwrite:
            from azure.core.exceptions import ResourceExistsError

            raise ResourceExistsError(message="exists")
        self._store[self._key] = data


class _FakeBlobServiceClient:
    def __init__(self, store: dict):
        self._store = store
        self.closed = False

    def get_blob_client(self, *, container: str, blob: str) -> _FakeBlobClient:
        return _FakeBlobClient(self._store, (container, blob))

    async def close(self) -> None:
        self.closed = True


async def test_blob_upload_writes_and_returns_uri():
    store: dict = {}
    factory = lambda: _FakeBlobServiceClient(store)  # noqa: E731
    p = AzureBlobProvider(
        base_url="https://acct.blob.core.windows.net",
        container="packs",
        client_factory=factory,
    )
    uri = await p.upload(content_hash="abc123", data=b"png-bytes", content_type="image/png")
    assert uri == "https://acct.blob.core.windows.net/packs/abc123"
    assert store[("packs", "abc123")] == b"png-bytes"


async def test_blob_upload_idempotent_on_existing_key():
    """`AC-PRECOMP-MIGR-2` precondition — re-uploading the same content
    hash must NOT raise; content is already addressed."""
    store: dict = {("packs", "k"): b"old"}
    factory = lambda: _FakeBlobServiceClient(store)  # noqa: E731
    p = AzureBlobProvider(
        base_url="https://acct.blob", container="packs", client_factory=factory,
    )
    # Should not raise even though the blob already exists.
    uri = await p.upload(content_hash="k", data=b"new", content_type="image/png")
    assert uri.endswith("/packs/k")
    # And the original content is preserved (overwrite=False).
    assert store[("packs", "k")] == b"old"


# ---------------------------------------------------------------------------
# Dual-write resolver — `AC-PRECOMP-MIGR-1`.
# ---------------------------------------------------------------------------


def test_dualwrite_prefers_blob_when_provider_says_blob():
    blob = AzureBlobProvider(base_url="https://x.blob", container="c")
    r = DualWriteResolver(blob=blob)
    a = _asset("blob", uri="https://x.blob/c/key1")
    assert r.resolve(a) == "https://x.blob/c/key1"


def test_dualwrite_falls_back_to_local_when_blob_provider_missing():
    """Blob desired but provider not configured → serve from local bytes
    (the dual-write safety net during the 7-day window)."""
    r = DualWriteResolver(blob=None)
    a = _asset("blob", uri="https://x.blob/c/key1", bytes_blob=b"x")
    assert r.resolve(a) == f"/api/v1/media/{a.id}"


def test_dualwrite_local_path_for_local_provider():
    r = DualWriteResolver()
    a = _asset("local", uri="/api/v1/media/x")
    assert r.resolve(a) == f"/api/v1/media/{a.id}"


def test_dualwrite_fal_passthrough():
    r = DualWriteResolver()
    a = _asset("fal", uri="https://fal/img.png")
    assert r.resolve(a) == "https://fal/img.png"


def test_dualwrite_unknown_provider_returns_storage_uri():
    r = DualWriteResolver()
    a = _asset("future", uri="https://elsewhere/asset")
    assert r.resolve(a) == "https://elsewhere/asset"
