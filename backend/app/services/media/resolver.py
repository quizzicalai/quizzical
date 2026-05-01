"""§21 Phase 5 — media provider switch.

`resolve_storage` is the single seam every writer (`builder`,
image_pipeline rehost path, future Azure migrator) must call to decide
**where** to put bytes and **what URI** to record. Read paths
(`/api/media/{id}` for `local`, direct CDN follow for `fal`) are
provider-agnostic — they only consume the URI written here.

`AC-PRECOMP-IMG-1`: the resolver returns a `StorageDecision` whose
shape is identical across providers — same fields, same types. Callers
must not branch on `provider` for anything beyond the `bytes_blob`
write decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from app.services.media.local_provider import build_local_uri, compute_content_hash

ProviderName = Literal["fal", "local"]


@dataclass(frozen=True)
class StorageDecision:
    """Where + what to persist for a freshly-generated asset."""

    provider: ProviderName
    storage_uri: str
    content_hash: str
    persist_bytes: bool
    """`True` when the writer should set `media_assets.bytes_blob`. False
    for the `fal` provider, which keeps the upstream CDN URL only."""


def resolve_storage(
    *,
    provider: ProviderName,
    asset_id: UUID | str,
    upstream_url: str | None,
    data: bytes | None,
) -> StorageDecision:
    """Pick the correct storage shape for `provider`.

    - `fal`   → keep `upstream_url` as the storage URI; never write bytes.
                `data` (if supplied) is hashed for dedup but discarded.
    - `local` → write bytes to the row; storage URI is
                `/api/media/{asset_id}`. `data` is required.

    Raises `ValueError` for invalid combinations (writer bug, never user
    input). The default provider stays `fal` so a missing config keeps
    today's behaviour exactly (`Universal-G5`).
    """
    aid = str(asset_id)
    if provider == "fal":
        if not upstream_url:
            raise ValueError("fal provider requires an upstream_url")
        ch = compute_content_hash(data) if data else ""
        return StorageDecision(
            provider="fal",
            storage_uri=upstream_url,
            content_hash=ch,
            persist_bytes=False,
        )
    if provider == "local":
        if data is None:
            raise ValueError("local provider requires `data` bytes to rehost")
        return StorageDecision(
            provider="local",
            storage_uri=build_local_uri(aid),
            content_hash=compute_content_hash(data),
            persist_bytes=True,
        )
    raise ValueError(f"unknown media provider: {provider!r}")
