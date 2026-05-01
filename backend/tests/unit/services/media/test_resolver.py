"""§21 Phase 5 — `media_resolver` provider switch (`AC-PRECOMP-IMG-1`)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.services.media.resolver import StorageDecision, resolve_storage


def test_storage_uri_renders_identically_across_providers():
    """Both providers return a `StorageDecision` with the same field shape
    so callers never branch on `provider` for anything beyond
    `persist_bytes`."""
    aid = uuid4()
    fal = resolve_storage(
        provider="fal",
        asset_id=aid,
        upstream_url="https://cdn.fal.ai/abc.png",
        data=None,
    )
    local = resolve_storage(
        provider="local",
        asset_id=aid,
        upstream_url=None,
        data=b"some-bytes",
    )
    assert isinstance(fal, StorageDecision)
    assert isinstance(local, StorageDecision)
    # Same field types
    assert {f.name for f in fal.__dataclass_fields__.values()} == {  # type: ignore[attr-defined]
        f.name for f in local.__dataclass_fields__.values()  # type: ignore[attr-defined]
    }


def test_fal_passthrough_keeps_upstream_uri():
    aid = uuid4()
    d = resolve_storage(
        provider="fal",
        asset_id=aid,
        upstream_url="https://cdn.fal.ai/x.png",
        data=None,
    )
    assert d.provider == "fal"
    assert d.storage_uri == "https://cdn.fal.ai/x.png"
    assert d.persist_bytes is False
    assert d.content_hash == ""


def test_local_provider_returns_relative_uri_and_hash():
    aid = uuid4()
    d = resolve_storage(
        provider="local",
        asset_id=aid,
        upstream_url=None,
        data=b"image-bytes",
    )
    assert d.provider == "local"
    assert d.storage_uri == f"/api/media/{aid}"
    assert d.persist_bytes is True
    assert len(d.content_hash) == 64  # SHA-256 hex


def test_local_provider_hashes_deterministically():
    a = resolve_storage(provider="local", asset_id=uuid4(), upstream_url=None, data=b"abc")
    b = resolve_storage(provider="local", asset_id=uuid4(), upstream_url=None, data=b"abc")
    assert a.content_hash == b.content_hash
    c = resolve_storage(provider="local", asset_id=uuid4(), upstream_url=None, data=b"def")
    assert a.content_hash != c.content_hash


def test_fal_without_upstream_url_raises():
    with pytest.raises(ValueError, match="upstream_url"):
        resolve_storage(provider="fal", asset_id=uuid4(), upstream_url=None, data=None)


def test_local_without_data_raises():
    with pytest.raises(ValueError, match="data"):
        resolve_storage(provider="local", asset_id=uuid4(), upstream_url=None, data=None)


def test_unknown_provider_rejected():
    with pytest.raises(ValueError, match="unknown"):
        resolve_storage(provider="azure", asset_id=uuid4(), upstream_url="x", data=None)  # type: ignore[arg-type]
