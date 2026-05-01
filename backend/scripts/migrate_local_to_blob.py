"""§21 Phase 12 — one-shot local→blob migrator (`AC-PRECOMP-MIGR-2`).

Run after the dual-write window closes. For every `media_assets` row
whose `storage_provider != 'blob'` and `bytes_blob IS NOT NULL`:

  1. Compute (or trust) `content_hash`.
  2. Upload bytes to Azure Blob via `AzureBlobProvider.upload`.
  3. UPDATE the row: `storage_provider='blob'`, `storage_uri=<blob url>`,
     `pending_rehost=False`. Bytes are deliberately retained until the
     follow-up `bytes_blob` drop migration; the blob URL is now the
     source of truth.

Rows whose source bytes are NULL are marked `pending_rehost=True` so
the async worker can re-derive them later (`AC-PRECOMP-MIGR-6`).

The migrator is **idempotent** — re-running over an already-migrated
table is a no-op (the WHERE filter excludes blob-backed rows). The
upload itself is content-addressed and idempotent on the blob side.

Returns a counters dict::

    {"migrated": N, "marked_pending": M, "skipped": K}
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select, update

from app.models.db import MediaAsset
from app.services.precompute.storage import AzureBlobProvider

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def migrate_local_to_blob(
    session: "AsyncSession",
    *,
    provider: AzureBlobProvider,
    batch_size: int = 100,
) -> dict[str, int]:
    """One-shot drain. Idempotent."""
    migrated = marked = skipped = 0

    while True:
        rows = (
            await session.execute(
                select(MediaAsset)
                .where(MediaAsset.storage_provider != "blob")
                .where(MediaAsset.storage_provider != "blob+cdn")
                .limit(batch_size)
            )
        ).scalars().all()
        if not rows:
            break

        for asset in rows:
            if asset.bytes_blob is None:
                # Source unreachable → defer to async worker.
                await session.execute(
                    update(MediaAsset)
                    .where(MediaAsset.id == asset.id)
                    .values(pending_rehost=True)
                )
                marked += 1
                continue

            content_type = _guess_content_type(asset.prompt_payload or {})
            try:
                uri = await provider.upload(
                    content_hash=asset.content_hash,
                    data=asset.bytes_blob,
                    content_type=content_type,
                )
            except Exception:
                # Don't crash the batch; flag the row for retry.
                await session.execute(
                    update(MediaAsset)
                    .where(MediaAsset.id == asset.id)
                    .values(pending_rehost=True)
                )
                skipped += 1
                continue

            await session.execute(
                update(MediaAsset)
                .where(MediaAsset.id == asset.id)
                .values(
                    storage_provider="blob",
                    storage_uri=uri,
                    pending_rehost=False,
                )
            )
            migrated += 1

        await session.commit()
        if len(rows) < batch_size:
            break

    return {"migrated": migrated, "marked_pending": marked, "skipped": skipped}


def _guess_content_type(payload: dict) -> str:
    fmt = (payload.get("format") or payload.get("mime") or "").lower()
    if fmt in ("png", "image/png"):
        return "image/png"
    if fmt in ("jpg", "jpeg", "image/jpeg"):
        return "image/jpeg"
    if fmt in ("webp", "image/webp"):
        return "image/webp"
    return "application/octet-stream"


__all__ = ["migrate_local_to_blob"]
