"""Build-time Q&A → brand-icon binder (DRAFT).

Mirrors ``app.services.precompute.lookup.PrecomputeLookup._vector_nn`` EXACTLY,
but against the icon index instead of ``topics``:

    embed(query) -> cosine vs every candidate -> argmax -> tau cutoff -> else None

Fidelity points (verified by tests):
  - Uses the SAME cosine math as ``lookup.py`` (``_default_cosine`` by default,
    injectable via ``cosine_fn`` like ``PrecomputeLookup``).
  - tau cutoff => below tau binds NOTHING (graceful no-icon), exactly like
    ``_vector_nn`` returning ``None`` below ``thresholds.match``.
  - The query string is prefixed with the BGE asymmetric retrieval instruction
    (``settings.images.query_prefix``); icon captions were embedded un-prefixed
    at seed time. This is the prototype Round 2 +4pt coverage win.
  - ``embed_fn`` is the async ``EmbedFn`` (``Callable[[str], Awaitable[list[float]
    | None]]``); empty query -> None -> no icon.

This module is imported only on the flag-ON path. It does NOT import the
embedder at module load — the embedder is passed in by the caller (the hook),
which constructs it lazily.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog

from app.services.icons.index import IconCandidate
from app.services.precompute.lookup import _default_cosine

logger = structlog.get_logger(__name__)

# Same shape as app.services.precompute.lookup.EmbedFn / CosineFn.
EmbedFn = Callable[[str], Awaitable[list[float] | None]]
CosineFn = Callable[[list[float], list[float]], float]


@dataclass(frozen=True)
class IconBinding:
    """Result of a HIT — the resolved icon id + provenance."""

    icon_id: str
    lucide: str
    concept: str
    palette_variant: str
    similarity: float


class IconBinder:
    """Resolve a Q&A string to an icon id via vector NN over the icon index.

    Construct one per build run with the candidate index + an async ``embed_fn``.
    The index is loaded once (from ``icon_assets`` via the DB, or the seed file);
    ``bind`` is then called per Q&A string.
    """

    def __init__(
        self,
        *,
        index: list[IconCandidate] | tuple[IconCandidate, ...],
        embed_fn: EmbedFn,
        tau: float,
        query_prefix: str = "",
        cosine_fn: CosineFn | None = None,
    ) -> None:
        self._index = list(index)
        self._embed_fn = embed_fn
        self._tau = float(tau)
        self._query_prefix = query_prefix or ""
        self._cosine_fn = cosine_fn or _default_cosine

    async def bind(self, text: str) -> IconBinding | None:
        """Mirror of ``_vector_nn``: embed, cosine-argmax over candidates, tau
        cutoff. Returns the chosen icon binding or ``None`` (graceful no-icon).
        """
        if not self._index:
            return None
        query = (self._query_prefix + text) if self._query_prefix else text
        query_emb = await self._embed_fn(query)
        if not query_emb:
            return None

        best: tuple[IconCandidate, float] | None = None
        for cand in self._index:
            sim = self._cosine_fn(list(query_emb), cand.embedding)
            if best is None or sim > best[1]:
                best = (cand, sim)

        if best is None or best[1] < self._tau:
            return None

        cand, sim = best
        return IconBinding(
            icon_id=cand.id,
            lucide=cand.lucide,
            concept=cand.concept,
            palette_variant=cand.palette_variant,
            similarity=round(float(sim), 4),
        )
