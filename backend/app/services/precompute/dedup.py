"""§21 Phase 3 — content & prompt deduplication helpers.

The hot-path build worker reuses already-stored artefacts whenever the
incoming text or image-prompt is byte-identical (after canonicalisation).
This module provides the small, deterministic hashing primitives those
checks rely on, plus the lookup helpers that the builder calls.

ACs:
- `AC-PRECOMP-DEDUP-1`: canonical-key reuse for characters.
- `AC-PRECOMP-DEDUP-2`: content-hash reuse for synopses / character_sets /
  baseline_sets / questions.
- `AC-PRECOMP-DEDUP-3`: prompt-hash reuse for `media_assets` (skip FAL).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import Character, MediaAsset
from app.services.precompute.canonicalize import canonical_key_for_name


def content_hash(payload: Any) -> str:
    """Stable SHA-256 hex digest over a JSON-serialisable payload.

    The payload is canonicalised via `json.dumps(..., sort_keys=True,
    separators=(",", ":"), ensure_ascii=False)` so equivalent dicts hash
    to the same value regardless of key order or whitespace.
    """
    if isinstance(payload, str):
        encoded = payload.encode("utf-8")
    else:
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def prompt_hash(prompt: str, *, provider: str = "", model: str = "") -> str:
    """Hash an image-generation prompt for `media_assets.prompt_hash` reuse.

    Provider + model are folded in so an identical prompt sent to a
    different model still produces a distinct asset; the same string sent
    to the same model collides and skips the FAL call.
    """
    seed = f"{provider}\x1f{model}\x1f{prompt or ''}"
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------


async def find_character_by_canonical_key(
    db: AsyncSession, name: str
) -> Character | None:
    """`AC-PRECOMP-DEDUP-1` — return the existing `Character` row whose
    `canonical_key` matches the canonicalised form of `name`, or None.

    Callers that find a hit should reuse the returned `Character.id` and
    skip the INSERT (and any expensive embedding / image-prompt work that
    would have followed it).
    """
    key = canonical_key_for_name(name or "")
    if not key:
        return None
    row = (
        await db.execute(
            select(Character).where(Character.canonical_key == key).limit(1)
        )
    ).scalar_one_or_none()
    return row


async def find_media_asset_by_prompt_hash(
    db: AsyncSession, *, prompt: str, provider: str = "", model: str = "",
    min_evaluator_score: int | None = None,
) -> MediaAsset | None:
    """`AC-PRECOMP-DEDUP-3` — reuse a previously-generated image when the
    `(prompt, provider, model)` triple has already produced an asset that
    cleared `min_evaluator_score`. Skip the FAL call when this returns a row.
    """
    h = prompt_hash(prompt, provider=provider, model=model)
    q = select(MediaAsset).where(MediaAsset.prompt_hash == h).limit(1)
    row = (await db.execute(q)).scalar_one_or_none()
    if row is None:
        return None
    if min_evaluator_score is not None:
        score = getattr(row, "evaluator_score", None)
        if score is None or int(score) < int(min_evaluator_score):
            return None
    return row


def coerce_uuid(value: Any) -> UUID | None:
    """Permissive UUID coercion used by dedup call sites that accept
    either string or UUID inputs from upstream JSON payloads."""
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None
