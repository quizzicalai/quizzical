"""§21 Phase 5 — `GET /api/media/{asset_id}` for locally-rehosted assets.

Behaviour:

- Looks up the `media_assets` row by id; 404 when missing or when the
  row has no `bytes_blob` (no local rehost yet — the client should keep
  using the upstream `storage_uri`).
- Returns the bytes with:
  - `Cache-Control: public, max-age=31536000, immutable`
    (`AC-PRECOMP-IMG-3`, `AC-PRECOMP-PERF-4`)
  - `ETag: "<content_hash>"` (strong validator; content-addressed asset
    so the hash is the canonical fingerprint).
  - `Content-Type` from `prompt_payload.content_type` when present,
    falling back to `image/png`.
- Honours `If-None-Match` with `304 Not Modified` and no body
  (`AC-PRECOMP-IMG-3`).
"""

from __future__ import annotations

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db_session
from app.core.config import settings
from app.models.db import MediaAsset

logger = logging.getLogger(__name__)
router = APIRouter(tags=["media"])

DEFAULT_CONTENT_TYPE = "image/png"


def _etag_value(content_hash: str) -> str:
    """RFC 7232 strong validator — wrapped in double quotes, no W/ prefix."""
    return f'"{content_hash}"'


def _content_type_for(asset: MediaAsset) -> str:
    payload = asset.prompt_payload or {}
    if isinstance(payload, dict):
        ct = payload.get("content_type")
        if isinstance(ct, str) and ct:
            return ct
    return DEFAULT_CONTENT_TYPE


@router.get(
    "/media/{asset_id}",
    summary="Serve a locally-rehosted media asset",
    response_class=Response,
)
async def get_media_asset(
    asset_id: UUID,
    db_session: Annotated[AsyncSession, Depends(get_db_session)],
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> Response:
    row = (
        await db_session.execute(select(MediaAsset).where(MediaAsset.id == asset_id))
    ).scalar_one_or_none()

    if row is None or row.bytes_blob is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)

    etag = _etag_value(row.content_hash)
    cache_control = settings.precompute.image_storage.cache_control
    common_headers = {
        "ETag": etag,
        "Cache-Control": cache_control,
        # Defence in depth — content-addressed bytes never change identity.
        "X-Content-Type-Options": "nosniff",
    }

    # Conditional GET — caller is asking us to confirm their cached copy.
    # Compare verbatim per RFC 7232 (servers MAY do weak compare; we use
    # strong since the asset is content-addressed).
    if if_none_match and etag in {tag.strip() for tag in if_none_match.split(",")}:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=common_headers)

    return Response(
        content=bytes(row.bytes_blob),
        media_type=_content_type_for(row),
        headers=common_headers,
        status_code=status.HTTP_200_OK,
    )
