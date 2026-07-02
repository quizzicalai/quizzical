"""Rehost ephemeral FAL CDN character images into ``media_assets`` (P1).

WHY (2026-07-02 audit)
----------------------
Every precomputed character image in prod is a live ``v3b.fal.media`` URL and
``media_assets`` is EMPTY — the local-rehost primitives (``bytes_blob``,
``/api/v1/media/{id}``, ``characters.image_asset_id``) exist but have ZERO
producers. When a FAL URL dies, the serve path silently REGENERATES the image
at quiz start (``image_pipeline._url_alive`` miss) — unbudgeted FAL spend and
art drift. This script is that missing producer:

  1. SELECT characters whose ``image_url`` points at a FAL host and that are
     not yet rehosted (resumable: already-rewritten rows never match).
  2. Download the bytes (SSRF-guarded via ``assert_url_safe``, bounded
     concurrency, no redirects) and verify an ``image/*`` content type.
  3. INSERT a ``media_assets`` row: ``bytes_blob``, sha256 ``content_hash``
     (dedup via ON CONFLICT DO NOTHING + re-select), ``storage_provider=
     'local'``, ``storage_uri='/api/v1/media/{id}'``, and the ORIGINAL FAL
     URL preserved under ``prompt_payload.rehost.source_url`` (rollback note).
  4. UPDATE ``characters`` — set ``image_asset_id`` AND rewrite ``image_url``
     to ``{public_base}/api/v1/media/{id}`` (the hydrator serves
     ``characters.image_url`` verbatim; the FE allowlist accepts the API
     host — see frontend/src/utils/safeImageUrl.ts).
  5. Mirror every ``session_history.character_set`` JSONB snapshot that
     contains the character (name-scoped, same shape as
     ``image_pipeline._refresh_character_set_images_batch``).

$0 FAL — this is download + store, never generation.

USAGE (from backend/, PROD_DB_URL or DATABASE_URL in env)
---------------------------------------------------------
    python -m scripts.rehost_fal_images --dry-run --limit 5
    python -m scripts.rehost_fal_images --limit 200 --batch-size 25
    python -m scripts.rehost_fal_images            # everything left

Rollback: each media_assets row keeps the original FAL URL in
``prompt_payload.rehost.source_url``; restoring is a single UPDATE joining
characters.image_asset_id -> media_assets.id.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

# The prod API host (Azure Container Apps ingress). Override with
# --public-base-url or API_PUBLIC_BASE_URL when the API moves.
DEFAULT_PUBLIC_BASE_URL = (
    "https://api-quizzical-dev.whitesea-815b33ea.westus2.azurecontainerapps.io"
)
MEDIA_PATH_FMT = "/api/v1/media/{asset_id}"

# Only rehost images from FAL CDN hosts (exact or subdomain).
FAL_HOST_SUFFIX = "fal.media"

ALLOWED_CONTENT_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/webp", "image/gif", "image/avif"}
)

FETCH_TIMEOUT_S = 30
MAX_IMAGE_BYTES = 8 * 1024 * 1024  # sanity cap; cast thumbs are ~100-300 KB


def _normalize_dsn(raw: str) -> str:
    """asyncpg needs ssl via connect_args; mirror audit_pack_image_coverage."""
    cleaned = re.sub(r"\?sslmode=[^&]+&?", "?", raw).rstrip("?&")
    return cleaned.replace("postgresql+psycopg://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )


def _is_fal_url(url: str) -> bool:
    from urllib.parse import urlparse

    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return False
    return host == FAL_HOST_SUFFIX or host.endswith("." + FAL_HOST_SUFFIX)


@dataclass
class RehostResult:
    character_id: str
    name: str
    status: str  # rehosted | deduped | dead-url | bad-content | error | dry-run
    asset_id: str | None = None
    note: str = ""


async def _fetch_image(url: str, client: Any) -> tuple[bytes, str] | None:
    """Download one image through the SSRF guard. Returns (bytes, content_type)
    or None when the URL is dead/unsafe/not-an-image. Never raises."""
    try:
        from app.services.precompute.outbound import assert_url_safe

        assert_url_safe(url)
        resp = await client.get(url, timeout=FETCH_TIMEOUT_S)
        if resp.status_code != 200:
            return None
        ctype = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        if ctype not in ALLOWED_CONTENT_TYPES:
            return None
        data = resp.content
        if not data or len(data) > MAX_IMAGE_BYTES:
            return None
        return data, ctype
    except Exception:
        return None


async def _upsert_media_asset(
    conn: Any,
    *,
    data: bytes,
    content_type: str,
    source_url: str,
) -> str:
    """Insert (or dedup onto) a media_assets row; returns the asset id.

    Dedup is content-addressed: sha256 over the raw bytes hits the UNIQUE
    ``content_hash`` constraint; ON CONFLICT DO NOTHING + re-select keeps the
    first row. The original FAL URL is preserved in prompt_payload.rehost.
    """
    from sqlalchemy import text

    content_hash = hashlib.sha256(data).hexdigest()
    row = (
        await conn.execute(
            text("SELECT id::text AS id, bytes_blob IS NOT NULL AS has_bytes "
                 "FROM media_assets WHERE content_hash = :ch"),
            {"ch": content_hash},
        )
    ).mappings().first()
    if row:
        if not row["has_bytes"]:
            # Row exists (e.g. earlier fal-provider write) but has no bytes —
            # complete the rehost in place.
            await conn.execute(
                text("UPDATE media_assets SET bytes_blob = :b WHERE id = :id"),
                {"b": data, "id": row["id"]},
            )
        return row["id"]

    asset_id = str(uuid.uuid4())
    payload = {
        "content_type": content_type,
        "rehost": {
            "source_url": source_url,
            "rehosted_at": datetime.now(UTC).isoformat(),
            "tool": "scripts.rehost_fal_images",
        },
    }
    await conn.execute(
        text(
            "INSERT INTO media_assets "
            "  (id, content_hash, prompt_hash, storage_provider, storage_uri, "
            "   bytes_blob, prompt_payload) "
            "VALUES (:id, :ch, :ph, 'local', :uri, :blob, CAST(:payload AS jsonb)) "
            "ON CONFLICT (content_hash) DO NOTHING"
        ),
        {
            "id": asset_id,
            "ch": content_hash,
            # No generation prompt is known for a rehost; key it to the source
            # URL so identical URLs map to a stable hash.
            "ph": hashlib.sha256(f"rehost:{source_url}".encode()).hexdigest(),
            "uri": MEDIA_PATH_FMT.format(asset_id=asset_id),
            "blob": data,
            "payload": json.dumps(payload),
        },
    )
    # A concurrent writer may have won the ON CONFLICT race — re-select.
    row = (
        await conn.execute(
            text("SELECT id::text AS id FROM media_assets WHERE content_hash = :ch"),
            {"ch": content_hash},
        )
    ).mappings().first()
    return row["id"] if row else asset_id


async def _rewrite_character(
    conn: Any, *, character_id: str, name: str, old_url: str,
    asset_id: str, new_url: str,
) -> None:
    from sqlalchemy import text

    await conn.execute(
        text(
            "UPDATE characters SET image_asset_id = :aid, image_url = :new_url, "
            "last_updated_at = now() "
            # Guard: only rewrite if the URL is still the one we downloaded
            # (a concurrent live-regen writing a fresh FAL URL wins).
            "WHERE id = :cid AND image_url = :old_url"
        ),
        {"aid": asset_id, "new_url": new_url, "cid": character_id, "old_url": old_url},
    )
    # Mirror session_history.character_set snapshots (name-scoped, exactly the
    # jsonb_set shape image_pipeline._refresh_character_set_images_batch uses).
    # The @> containment predicate skips sessions that never had this character.
    await conn.execute(
        text(
            """
            UPDATE session_history
            SET character_set = (
                SELECT COALESCE(jsonb_agg(
                    CASE WHEN elem->>'name' = :name
                         THEN jsonb_set(elem, '{image_url}', to_jsonb(CAST(:url AS text)))
                         ELSE elem
                    END
                ), '[]'::jsonb)
                FROM jsonb_array_elements(character_set) elem
            ),
            last_updated_at = now()
            WHERE character_set @> jsonb_build_array(jsonb_build_object('name', CAST(:name AS text)))
            """
        ),
        {"name": name, "url": new_url},
    )


async def run(
    *,
    db_url: str,
    public_base_url: str,
    limit: int,
    batch_size: int,
    concurrency: int,
    dry_run: bool,
) -> dict[str, Any]:
    import httpx
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(_normalize_dsn(db_url), connect_args={"ssl": True})
    base = public_base_url.rstrip("/")
    results: list[RehostResult] = []
    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT id::text AS id, name, image_url "
                        "FROM characters "
                        "WHERE image_url IS NOT NULL AND image_url <> '' "
                        # Resumable: rewritten rows no longer look like FAL URLs.
                        "AND image_url LIKE 'https://%' "
                        "AND image_url NOT LIKE :base_like "
                        "ORDER BY last_updated_at ASC "
                        + ("LIMIT :lim" if limit > 0 else "")
                    ),
                    {"base_like": f"{base}%", **({"lim": limit} if limit > 0 else {})},
                )
            ).mappings().all()
        candidates = [r for r in rows if _is_fal_url(r["image_url"])]
        print(f"candidates: {len(candidates)} (of {len(rows)} https rows scanned)")

        sem = asyncio.Semaphore(max(1, concurrency))

        async with httpx.AsyncClient(follow_redirects=False) as client:
            for start in range(0, len(candidates), max(1, batch_size)):
                batch = candidates[start : start + max(1, batch_size)]

                async def _download(r: Any) -> tuple[Any, tuple[bytes, str] | None]:
                    async with sem:
                        return r, await _fetch_image(r["image_url"], client)

                downloaded = await asyncio.gather(*[_download(r) for r in batch])

                if dry_run:
                    for r, payload in downloaded:
                        results.append(
                            RehostResult(
                                character_id=r["id"], name=r["name"],
                                status="dry-run" if payload else "dead-url",
                                note=f"{len(payload[0])}B {payload[1]}" if payload else "",
                            )
                        )
                    continue

                # One transaction per batch: crash mid-run loses at most a batch.
                async with engine.begin() as conn:
                    for r, payload in downloaded:
                        if payload is None:
                            results.append(
                                RehostResult(
                                    character_id=r["id"], name=r["name"],
                                    status="dead-url",
                                    note="fetch failed / not image/*",
                                )
                            )
                            continue
                        data, ctype = payload
                        try:
                            asset_id = await _upsert_media_asset(
                                conn, data=data, content_type=ctype,
                                source_url=r["image_url"],
                            )
                            new_url = base + MEDIA_PATH_FMT.format(asset_id=asset_id)
                            await _rewrite_character(
                                conn,
                                character_id=r["id"], name=r["name"],
                                old_url=r["image_url"],
                                asset_id=asset_id, new_url=new_url,
                            )
                            results.append(
                                RehostResult(
                                    character_id=r["id"], name=r["name"],
                                    status="rehosted", asset_id=asset_id,
                                )
                            )
                        except Exception as e:  # keep the batch going
                            results.append(
                                RehostResult(
                                    character_id=r["id"], name=r["name"],
                                    status="error", note=str(e)[:160],
                                )
                            )
                done = start + len(batch)
                print(f"progress: {done}/{len(candidates)}", flush=True)
    finally:
        await engine.dispose()

    counts: dict[str, int] = {}
    for res in results:
        counts[res.status] = counts.get(res.status, 0) + 1
    dead = [r for r in results if r.status == "dead-url"]
    errs = [r for r in results if r.status == "error"]
    summary = {
        "counts": counts,
        "dead_urls": [{"name": r.name, "id": r.character_id} for r in dead],
        "errors": [{"name": r.name, "note": r.note} for r in errs],
        "sample_asset_ids": [r.asset_id for r in results if r.asset_id][:5],
    }
    return summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--limit", type=int, default=0,
                   help="max characters to process this run (0 = all)")
    p.add_argument("--batch-size", type=int, default=25,
                   help="characters per DB transaction (default 25)")
    p.add_argument("--concurrency", type=int, default=8,
                   help="parallel downloads (default 8)")
    p.add_argument("--dry-run", action="store_true",
                   help="download + verify only; write nothing")
    p.add_argument("--public-base-url",
                   default=os.environ.get("API_PUBLIC_BASE_URL", DEFAULT_PUBLIC_BASE_URL),
                   help="public API origin used to build the rewritten image_url")
    p.add_argument("--json", type=argparse.FileType("w", encoding="utf-8"),
                   default=None, help="write the JSON summary here")
    args = p.parse_args(argv)

    db_url = os.environ.get("PROD_DB_URL") or os.environ.get("DATABASE_URL")
    if not db_url:
        print("error: set PROD_DB_URL (or DATABASE_URL)", file=sys.stderr)
        return 2

    summary = asyncio.run(
        run(
            db_url=db_url,
            public_base_url=args.public_base_url,
            limit=args.limit,
            batch_size=args.batch_size,
            concurrency=args.concurrency,
            dry_run=args.dry_run,
        )
    )
    print(json.dumps(summary, indent=2))
    if args.json:
        json.dump(summary, args.json, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
