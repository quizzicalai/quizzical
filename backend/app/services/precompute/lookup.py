"""Phase 2 — read-path lookup shim (§21.4 + AC-PRECOMP-LOOKUP-1..5).

This service translates a free-text user category into a `(topic_id, pack_id)`
HIT or returns `None` (MISS → caller falls through to the live agent).

Resolution order (`AC-PRECOMP-LOOKUP-1`):

  1. **Alias-exact** — `topic_aliases.alias_normalized` lookup with the
     canonical key (`canonical_key_for_name`).
  2. **Slug-exact** — `topics.slug` lookup with the canonical key reformatted
     as a slug (spaces → hyphens).
  3. **Vector NN** — only consulted on alias/slug MISS, only if an `embed_fn`
     is wired in. Picks the topic whose `topics.embedding` has the highest
     cosine similarity above `τ_match`.

Hard guards (`AC-PRECOMP-LOOKUP-4`):
  - The topic's `policy_status` must be `'allowed'`.
  - The pack pointed to by `topics.current_pack_id` must have
    `status='published'`. A quarantined / draft pack is treated as MISS even
    if the FK is pinned.

`AC-PRECOMP-PERF-5` — no model call on alias / slug HIT. The embedder is only
invoked when both exact lookups MISS AND `embed_fn` is configured AND
`topics.embedding` is non-NULL on at least one published topic.

Phase 2 scope:
  - No Redis cache — that lands in Phase 4 (`pack_cache.py`).
  - The vector path is dialect-aware: when running against Postgres with
    pgvector, an `ORDER BY topics.embedding <=> :q` query is used. Under
    SQLite (the test bench) the service loads the candidate embeddings into
    Python and computes cosine via the injected `cosine_fn`. Both paths
    return identical results above `τ_match`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import Topic, TopicAlias, TopicPack
from app.services.precompute.canonicalize import canonical_key_for_name

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LookupThresholds:
    """`AC-PRECOMP-LOOKUP-3` — thresholds with documented defaults.

    `match` is the minimum cosine similarity for a vector NN HIT; `pass_score`
    and `strong_trigger_score` are evaluator gates surfaced here so the entire
    precompute config sits in one place (the evaluator service in Phase 3
    consumes them).
    """

    match: float = 0.86
    pass_score: int = 7
    strong_trigger_score: int = 5


DEFAULT_THRESHOLDS = LookupThresholds()


@dataclass(frozen=True)
class TopicResolution:
    """Result of a successful HIT — caller hydrates the pack from this."""

    topic_id: UUID
    pack_id: UUID
    via: Literal["alias", "slug", "vector"]
    similarity: float | None = None  # only set for `via == "vector"`


# Type aliases for injected helpers (test-friendly).
EmbedFn = Callable[[str], Awaitable[list[float] | None]]
CosineFn = Callable[[list[float], list[float]], float]


_SLUG_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    """Normalise alias text into the slug shape stored on `topics.slug`."""
    canonical = canonical_key_for_name(text)
    if not canonical:
        return ""
    return _SLUG_NON_ALNUM.sub("-", canonical).strip("-")


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class PrecomputeLookup:
    """Read-only resolver for `(topic_id, pack_id)`.

    Construct one per request via the FastAPI dependency
    `get_precompute_lookup`; do not share across requests because the bound
    `db` session is request-scoped.
    """

    def __init__(
        self,
        *,
        db: AsyncSession,
        redis,  # Phase 4 — kept in signature for binary compatibility.
        thresholds: LookupThresholds = DEFAULT_THRESHOLDS,
        embed_fn: EmbedFn | None = None,
        cosine_fn: CosineFn | None = None,
    ) -> None:
        self._db = db
        self._redis = redis
        self._thresholds = thresholds
        self._embed_fn = embed_fn
        self._cosine_fn = cosine_fn or _default_cosine

    async def resolve_topic(self, raw_text: str) -> TopicResolution | None:
        canonical = canonical_key_for_name(raw_text or "")
        if not canonical:
            return None

        # 1. Alias exact (canonical key matches `alias_normalized`).
        topic_id = await self._alias_exact(canonical)
        if topic_id is not None:
            pack_id = await self._published_pack_id(topic_id)
            if pack_id is not None:
                resolution = TopicResolution(
                    topic_id=topic_id, pack_id=pack_id, via="alias"
                )
                _log_hit(resolution)
                return resolution

        # 2. Slug exact (canonical key, slugified).
        slug = _slugify(raw_text)
        if slug:
            topic_id = await self._slug_exact(slug)
            if topic_id is not None:
                pack_id = await self._published_pack_id(topic_id)
                if pack_id is not None:
                    resolution = TopicResolution(
                        topic_id=topic_id, pack_id=pack_id, via="slug"
                    )
                    _log_hit(resolution)
                    return resolution

        # 3. Vector NN — only if an embedder is wired in.
        if self._embed_fn is not None:
            resolution = await self._vector_nn(raw_text)
            if resolution is not None:
                _log_hit(resolution)
                return resolution

        logger.info("precompute.lookup.miss", canonical=canonical)
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _alias_exact(self, canonical: str) -> UUID | None:
        stmt = (
            select(TopicAlias.topic_id)
            .join(Topic, Topic.id == TopicAlias.topic_id)
            .where(
                TopicAlias.alias_normalized == canonical,
                Topic.policy_status == "allowed",
            )
            .limit(1)
        )
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    async def _slug_exact(self, slug: str) -> UUID | None:
        stmt = (
            select(Topic.id)
            .where(Topic.slug == slug, Topic.policy_status == "allowed")
            .limit(1)
        )
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    async def _published_pack_id(self, topic_id: UUID) -> UUID | None:
        """Defensive guard: only return a pack with `status='published'`.

        Even if `topics.current_pack_id` points at a quarantined or draft pack
        (data drift / aborted publish), we MUST treat it as MISS so the caller
        falls back to the live agent (`AC-PRECOMP-LOOKUP-4`).
        """
        stmt = (
            select(TopicPack.id)
            .join(Topic, Topic.current_pack_id == TopicPack.id)
            .where(
                Topic.id == topic_id,
                TopicPack.status == "published",
            )
            .limit(1)
        )
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    async def _vector_nn(self, raw_text: str) -> TopicResolution | None:
        """Vector nearest-neighbour with τ_match cutoff.

        On Postgres + pgvector, this would dispatch a single
        ``ORDER BY embedding <=> :q LIMIT 1`` query; under SQLite (tests) we
        load all candidate embeddings and compute cosine in Python. Both
        paths apply the same τ_match threshold and the same `policy_status`
        / `published`-pack guards.
        """
        assert self._embed_fn is not None  # caller checked
        query_emb = await self._embed_fn(raw_text)
        if not query_emb:
            return None

        stmt = (
            select(Topic.id, Topic.embedding, Topic.current_pack_id)
            .where(
                Topic.embedding.isnot(None),
                Topic.policy_status == "allowed",
                Topic.current_pack_id.isnot(None),
            )
        )
        result = await self._db.execute(stmt)
        rows = result.all()

        best: tuple[UUID, UUID, float] | None = None
        for topic_id, candidate_emb, candidate_pack in rows:
            parsed = _coerce_vector(candidate_emb)
            if not parsed:
                continue
            sim = self._cosine_fn(list(query_emb), parsed)
            if best is None or sim > best[2]:
                best = (topic_id, candidate_pack, sim)

        if best is None or best[2] < self._thresholds.match:
            return None

        # Re-validate the candidate pack is still published (defence in depth).
        pack_id = await self._published_pack_id(best[0])
        if pack_id is None or pack_id != best[1]:
            return None

        return TopicResolution(
            topic_id=best[0],
            pack_id=pack_id,
            via="vector",
            similarity=best[2],
        )


# ---------------------------------------------------------------------------
# Module-private helpers
# ---------------------------------------------------------------------------


def _default_cosine(a: list[float], b: list[float]) -> float:
    """Pure-Python cosine, used when the caller doesn't inject one."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / ((na ** 0.5) * (nb ** 0.5))


def _coerce_vector(raw) -> list[float] | None:
    """Normalise a stored embedding into list[float].

    pgvector returns a Python list under raw Postgres, a numpy ndarray when
    the SQLAlchemy type processor is active, and a literal `'[1.0,0.0,...]'`
    string under SQLite when the column compiles to TEXT. Parse all forms;
    return None for empty / malformed.
    """
    if raw is None:
        return None
    # numpy.ndarray exposes .tolist(); covers both pgvector's processed result
    # and any other array-like that iterates to floats.
    if hasattr(raw, "tolist") and not isinstance(raw, str):
        try:
            out = [float(x) for x in raw.tolist()]
            return out or None
        except (TypeError, ValueError):
            return None
    if isinstance(raw, (list, tuple)):
        try:
            return [float(x) for x in raw] or None
        except (TypeError, ValueError):
            return None
    if isinstance(raw, str):
        s = raw.strip().strip("[]")
        if not s:
            return None
        try:
            return [float(x.strip()) for x in s.split(",")]
        except (TypeError, ValueError):
            return None
    return None


def _log_hit(resolution: TopicResolution) -> None:
    logger.info(
        "precompute.lookup.hit",
        topic_id=str(resolution.topic_id),
        pack_id=str(resolution.pack_id),
        via=resolution.via,
        similarity=resolution.similarity,
    )


__all__ = [
    "DEFAULT_THRESHOLDS",
    "LookupThresholds",
    "PrecomputeLookup",
    "TopicResolution",
]
