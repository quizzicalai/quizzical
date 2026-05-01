"""§21 Phase 7 — embeddings cache.

`AC-PRECOMP-COST-1`: every text embedding is deduplicated on a SHA-256
`text_hash` keyed against `embeddings_cache(text_hash, model, dim,
embedding)`. New text → embed once, ever; subsequent lookups for the same
text return the cached vector without invoking `embed_fn`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import EmbeddingsCache

EmbedFn = Callable[[str], Awaitable[list[float]]]


def text_hash(text: str) -> str:
    """SHA-256 hex of the UTF-8 bytes — stable across processes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def get_or_compute_embedding(
    session: AsyncSession,
    text: str,
    *,
    model: str,
    dim: int,
    embed_fn: EmbedFn,
) -> list[float]:
    """Return a cached vector for `text` or compute + persist a fresh one.

    On miss: calls `embed_fn(text)` exactly once, writes the row, returns
    the vector. On hit: never calls `embed_fn`.
    """
    h = text_hash(text)
    row = (
        await session.execute(
            select(EmbeddingsCache).where(EmbeddingsCache.text_hash == h)
        )
    ).scalar_one_or_none()
    if row is not None:
        return list(row.embedding) if row.embedding is not None else []

    vec = await embed_fn(text)
    session.add(
        EmbeddingsCache(
            text_hash=h, model=model, dim=dim, embedding=list(vec),
        )
    )
    await session.flush()
    return list(vec)
