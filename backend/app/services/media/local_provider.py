"""§21 Phase 5 — local media provider.

Stores raw bytes on the `media_assets` row (`bytes_blob`) and returns a
relative URI of the form `/api/media/{asset_id}`. The local provider is
intentionally synchronous on the DB side because byte payloads are small
(< 1 MB after FAL Schnell 512×512 PNG compression) and the rehost path
runs on the worker, not the request hot path.

Content addressing: `content_hash` is a hex-encoded SHA-256 over the
raw bytes. Two callers that hash the same payload land on the same row,
which keeps the `media_assets.content_hash UNIQUE` constraint cheap.
"""

from __future__ import annotations

import hashlib

LOCAL_URI_FMT = "/api/media/{asset_id}"


def compute_content_hash(data: bytes) -> str:
    """Hex SHA-256 of the asset bytes. Used for both the `content_hash`
    column (dedup key) and the served `ETag` header
    (`AC-PRECOMP-PERF-4`)."""
    return hashlib.sha256(data).hexdigest()


def build_local_uri(asset_id: str) -> str:
    """Build the served URL for a locally-rehosted asset. Returned as a
    site-relative path so it works under any host / TLS config without
    leaking environment details."""
    return LOCAL_URI_FMT.format(asset_id=asset_id)
