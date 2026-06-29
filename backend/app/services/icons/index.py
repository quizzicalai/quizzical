"""Icon index loading for the Q&A icon binder (DRAFT).

The binder needs the candidate icon set: each entry's stable id + its 384-dim
caption embedding (same space as ``topics.embedding``). The authoritative store
is the ``icon_assets`` table (seeded by ``seed.py`` from the prototype's
validated ``data/icon_index.json``). ``load_icon_index_from_db`` reads it via
the request-scoped ``AsyncSession``, mirroring how ``lookup.py::_vector_nn``
reads ``topics``.

A file-backed loader (``load_icon_index_from_file``) is provided for tests /
offline build paths and as a defensive fallback, but the live build path reads
from the DB so a re-seed never requires a redeploy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import IconAsset

# Embeddings are coerced with the SAME helper the topic NN path uses, so the
# pgvector/numpy/sqlite-TEXT forms are parsed identically.
from app.services.precompute.lookup import _coerce_vector

_SEED_PATH = Path(__file__).resolve().parent / "data" / "icon_index.json"


@dataclass(frozen=True)
class IconCandidate:
    """One routable icon: stable id + metadata + 384-dim caption embedding."""

    id: str
    lucide: str
    concept: str
    caption: str
    palette_variant: str
    embedding: list[float]


async def load_icon_index_from_db(db: AsyncSession) -> list[IconCandidate]:
    """Load all routable icons from ``icon_assets`` (the authoritative store).

    Rows with a missing / malformed embedding are skipped (defence in depth),
    matching ``_vector_nn``'s ``_coerce_vector`` guard.
    """
    stmt = select(
        IconAsset.id,
        IconAsset.lucide_name,
        IconAsset.concept,
        IconAsset.caption,
        IconAsset.palette_variant,
        IconAsset.embedding,
    )
    rows = (await db.execute(stmt)).all()
    out: list[IconCandidate] = []
    for icon_id, lucide, concept, caption, variant, emb in rows:
        parsed = _coerce_vector(emb)
        if not parsed:
            continue
        out.append(
            IconCandidate(
                id=icon_id,
                lucide=lucide,
                concept=concept,
                caption=caption,
                palette_variant=variant,
                embedding=parsed,
            )
        )
    return out


def _read_seed(path: Path) -> list[IconCandidate]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[IconCandidate] = []
    for ic in data.get("icons", []):
        emb = _coerce_vector(ic.get("embedding"))
        if not emb:
            continue
        out.append(
            IconCandidate(
                id=ic["id"],
                lucide=ic.get("lucide", ic["id"]),
                concept=ic.get("concept", ""),
                caption=ic.get("caption", ""),
                palette_variant=ic.get("palette_variant", "sea"),
                embedding=emb,
            )
        )
    return out


@lru_cache(maxsize=1)
def load_icon_index_from_file(path: str | None = None) -> tuple[IconCandidate, ...]:
    """Load the seed icon index from JSON (tests / offline build / fallback).

    Cached because the seed file is immutable for a given process. Returns a
    tuple so the cached value cannot be mutated by callers.
    """
    p = Path(path) if path else _SEED_PATH
    return tuple(_read_seed(p))


def seed_path() -> Path:
    """Absolute path to the bundled seed icon index JSON."""
    return _SEED_PATH
