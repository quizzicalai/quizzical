"""§21 Phase 12 — one-shot local→blob migrator (`AC-PRECOMP-MIGR-2`)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.models.db import MediaAsset
from app.services.precompute.storage import AzureBlobProvider
from scripts.migrate_local_to_blob import migrate_local_to_blob

pytestmark = pytest.mark.anyio


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

    def get_blob_client(self, *, container: str, blob: str) -> _FakeBlobClient:
        return _FakeBlobClient(self._store, (container, blob))

    async def close(self) -> None:
        pass


def _provider(store: dict) -> AzureBlobProvider:
    return AzureBlobProvider(
        base_url="https://acct.blob",
        container="packs",
        client_factory=lambda: _FakeBlobServiceClient(store),
    )


def _asset(*, provider: str, bytes_blob: bytes | None, ch: str | None = None) -> MediaAsset:
    return MediaAsset(
        id=uuid.uuid4(),
        content_hash=ch or "h-" + uuid.uuid4().hex,
        prompt_hash="p-" + uuid.uuid4().hex,
        storage_provider=provider,
        storage_uri="" if provider == "local" else "https://elsewhere/x",
        bytes_blob=bytes_blob,
        prompt_payload={"format": "png"},
    )


async def test_local_with_bytes_migrates_and_flips_provider(sqlite_db_session):
    store: dict = {}
    a = _asset(provider="local", bytes_blob=b"png-bytes")
    sqlite_db_session.add(a)
    await sqlite_db_session.commit()

    out = await migrate_local_to_blob(sqlite_db_session, provider=_provider(store))
    assert out == {"migrated": 1, "marked_pending": 0, "skipped": 0}

    await sqlite_db_session.refresh(a)
    assert a.storage_provider == "blob"
    assert a.storage_uri.endswith(f"/packs/{a.content_hash}")
    assert a.pending_rehost is False
    assert store[("packs", a.content_hash)] == b"png-bytes"


async def test_row_without_bytes_marked_pending(sqlite_db_session):
    store: dict = {}
    a = _asset(provider="local", bytes_blob=None)
    sqlite_db_session.add(a)
    await sqlite_db_session.commit()

    out = await migrate_local_to_blob(sqlite_db_session, provider=_provider(store))
    assert out["marked_pending"] == 1 and out["migrated"] == 0

    await sqlite_db_session.refresh(a)
    assert a.pending_rehost is True
    assert a.storage_provider == "local"  # untouched until worker re-derives.


async def test_blob_rows_are_skipped_idempotently(sqlite_db_session):
    """Re-running the migrator over a blob-backed table is a no-op."""
    store: dict = {}
    a = _asset(provider="blob", bytes_blob=b"x")
    a.storage_uri = "https://acct.blob/packs/abc"
    sqlite_db_session.add(a)
    await sqlite_db_session.commit()

    out = await migrate_local_to_blob(sqlite_db_session, provider=_provider(store))
    assert out == {"migrated": 0, "marked_pending": 0, "skipped": 0}
    assert store == {}


async def test_mixed_batch(sqlite_db_session):
    store: dict = {}
    rows = [
        _asset(provider="local", bytes_blob=b"a"),
        _asset(provider="local", bytes_blob=None),
        _asset(provider="local", bytes_blob=b"c"),
        _asset(provider="blob", bytes_blob=b"already"),
    ]
    rows[3].storage_uri = "https://acct.blob/packs/already"
    for r in rows:
        sqlite_db_session.add(r)
    await sqlite_db_session.commit()

    out = await migrate_local_to_blob(sqlite_db_session, provider=_provider(store), batch_size=10)
    assert out["migrated"] == 2
    assert out["marked_pending"] == 1
    assert out["skipped"] == 0


async def test_idempotent_when_re_run(sqlite_db_session):
    store: dict = {}
    a = _asset(provider="local", bytes_blob=b"png-bytes")
    sqlite_db_session.add(a)
    await sqlite_db_session.commit()

    await migrate_local_to_blob(sqlite_db_session, provider=_provider(store))
    out2 = await migrate_local_to_blob(sqlite_db_session, provider=_provider(store))
    assert out2 == {"migrated": 0, "marked_pending": 0, "skipped": 0}

    rows = (await sqlite_db_session.execute(select(MediaAsset))).scalars().all()
    assert len(rows) == 1 and rows[0].storage_provider == "blob"
