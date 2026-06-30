"""Per-string RELEVANCE GATE for same-universe Q&A image generation (PRIORITY 2).

This is the make-or-break guardrail the owner flagged: the app produces a VAST
range of outcomes/topics, but most Q&A *strings* in a personality quiz are
ABSTRACT prompts ("It's Saturday afternoon. Where are you most likely to be?",
"Curled up with a thick book in a quiet corner of the library") — not concrete,
universe-anchored, depictable scenes. Generating a FAL image for an abstract
string burns budget on a weak, off-topic picture. The gate decides, per string,
whether it is concrete/depictable ENOUGH to deserve a same-universe FAL image;
otherwise the string falls back to the $0 generic-icon binder (or no image).

Design (reuses the EXISTING 384-dim embedder + cosine — no new model):

  * Two small curated ANCHOR sets, embedded once with the SAME
    ``BAAI/bge-small-en-v1.5`` model the icon binder uses:
      - CONCRETE anchors  — phrasings of "a depictable object/scene/character".
      - ABSTRACT anchors  — phrasings of "a feeling/preference/self-assessment".
  * For a Q&A string we embed it (BGE query-prefixed, like the binder), take the
    max cosine to each anchor set, and gate on:
      score = max_sim(concrete) - max_sim(abstract)  >=  margin   (the MARGIN gate)
      AND   max_sim(concrete)                        >=  floor    (a sanity floor)
  * Very short / template-y strings ("None of the above", "It depends") are
    skipped cheaply before any embed.

The gate is PURE w.r.t. its anchors (computed once, cached) and async only
because the embedder is async. It NEVER raises into the build: any error =>
``GateDecision(generate=False, reason="error")`` so the string safely falls back.

Thresholds live in ``settings.images.relevance_gate`` (``RelevanceGateConfig``)
so they can be tuned without a code change. Operating point validated offline on
a diverse labeled Q&A sample (see ``specifications/prototype/``); report numbers
in ``QA-SAME-UNIVERSE-RESULTS.md``.
"""

from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog

from app.services.precompute.lookup import _default_cosine

logger = structlog.get_logger(__name__)

EmbedFn = Callable[[str], Awaitable[list[float] | None]]
CosineFn = Callable[[list[float], list[float]], float]

# ---------------------------------------------------------------------------
# Curated anchor captions. These are deliberately SHORT, generic phrasings of
# the two poles — they are NOT topic-specific (the universe anchoring happens in
# the prompt builder), they only separate "this reads like a thing you can draw"
# from "this reads like a feeling / preference / self-description". Kept in code
# (not the DB) because they are part of the gate's semantics, versioned with it.
# ---------------------------------------------------------------------------

CONCRETE_ANCHORS: tuple[str, ...] = (
    "a detailed illustration of a specific animal or creature",
    "a painting of a landscape, mountain, ocean, or sky",
    "a drawing of a building, castle, temple, or monument",
    "a picture of a physical object, tool, weapon, or instrument",
    "an illustration of a named character in costume",
    "a picture of a vehicle such as a ship, car, plane, or spacecraft",
    "a photo of food or a drink on a plate or in a glass",
    "a botanical drawing of a plant, tree, flower, or cactus",
    "a depiction of weather, clouds, a storm, or a star in the sky",
    "an image of a person doing a hands-on craft or trade",
    "a close-up of an artifact, gem, lens, or piece of equipment",
    "a scene set in a recognisable place with concrete things in it",
)

ABSTRACT_ANCHORS: tuple[str, ...] = (
    "how you feel about something",
    "your personal preference or favourite thing",
    "a question about your personality or temperament",
    "what you value most in life",
    "a description of your own behaviour, habits, or mood",
    "an abstract idea, value, or concept with no physical shape",
    "rating how strongly you agree with a statement",
    "a hypothetical life choice with no scene to picture",
    "what motivates you or drives your decisions",
    "a self-assessment of your strengths and weaknesses",
    "none of the above, both, neither, or it depends",
    "how you spend your free time or react to a situation",
)

# Strings shorter than this (after strip) are too thin to depict well — skip.
_MIN_CHARS = 6
# Obvious non-depictable template answers, matched case-insensitively as a whole
# string. Cheap pre-filter before any embedding work.
_TEMPLATE_SKIPS = frozenset(
    {
        "none of the above",
        "all of the above",
        "it depends",
        "other",
        "n/a",
        "none",
        "both",
        "neither",
        "not sure",
        "i don't know",
        "maybe",
    }
)


@dataclass(frozen=True)
class GateDecision:
    """Why a Q&A string was (not) routed to same-universe generation."""

    generate: bool
    reason: str
    concrete_sim: float = 0.0
    abstract_sim: float = 0.0

    @property
    def margin(self) -> float:
        return round(self.concrete_sim - self.abstract_sim, 4)


class _AnchorCache:
    """Process-wide, lazily-computed anchor embeddings keyed by (embedder id).

    Computed once per process via the SAME embedder the binder uses. Stored as a
    plain list of vectors; tiny (24 vectors x 384 floats)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._concrete: list[list[float]] | None = None
        self._abstract: list[list[float]] | None = None
        self._key: object | None = None

    async def get(
        self, embed_fn: EmbedFn, query_prefix: str
    ) -> tuple[list[list[float]], list[list[float]]]:
        # Key on the embed_fn identity so a different embedder re-computes.
        if self._concrete is not None and self._key is embed_fn:
            return self._concrete, self._abstract  # type: ignore[return-value]

        async def _embed_all(texts: tuple[str, ...]) -> list[list[float]]:
            out: list[list[float]] = []
            for t in texts:
                # Anchors are the "documents" side of asymmetric retrieval, so
                # they are embedded UN-prefixed (mirrors how icon captions are
                # seeded un-prefixed and only the Q&A query gets the prefix).
                vec = await embed_fn(t)
                if vec:
                    out.append(list(vec))
            return out

        concrete = await _embed_all(CONCRETE_ANCHORS)
        abstract = await _embed_all(ABSTRACT_ANCHORS)
        with self._lock:
            self._concrete = concrete
            self._abstract = abstract
            self._key = embed_fn
        return concrete, abstract


_ANCHORS = _AnchorCache()


def _looks_template(text: str) -> bool:
    return text.strip().lower().rstrip(".!?") in _TEMPLATE_SKIPS


def _max_sim(query: list[float], anchors: list[list[float]], cosine: CosineFn) -> float:
    best = 0.0
    for a in anchors:
        s = cosine(query, a)
        if s > best:
            best = s
    return best


class RelevanceGate:
    """Decide per Q&A string whether to route it to same-universe FAL generation.

    Construct one per build with the async ``embed_fn`` (the binder's
    ``raw_embed``), the BGE ``query_prefix`` (so the query is embedded the same
    way the binder embeds it), and the tuned ``margin`` / ``concrete_floor``.
    """

    def __init__(
        self,
        *,
        embed_fn: EmbedFn,
        query_prefix: str = "",
        margin: float = 0.04,
        concrete_floor: float = 0.20,
        cosine_fn: CosineFn | None = None,
    ) -> None:
        self._embed_fn = embed_fn
        self._query_prefix = query_prefix or ""
        self._margin = float(margin)
        self._concrete_floor = float(concrete_floor)
        self._cosine = cosine_fn or _default_cosine

    async def score(self, text: str) -> GateDecision:
        """Return the gate decision for one Q&A string. NEVER raises."""
        try:
            return await self._score(text)
        except Exception:  # noqa: BLE001 — gate must fail SAFE (no generation)
            logger.warning("qa_image.gate.error", exc_info=True)
            return GateDecision(generate=False, reason="error")

    async def _score(self, text: str) -> GateDecision:
        if not (isinstance(text, str) and text.strip()):
            return GateDecision(generate=False, reason="blank")
        # Template check FIRST: "n/a" / "other" are short but should be reported
        # as template skips, and they must short-circuit before any embed.
        if _looks_template(text):
            return GateDecision(generate=False, reason="template")
        if len(text.strip()) < _MIN_CHARS:
            return GateDecision(generate=False, reason="too_short")

        query = (self._query_prefix + text) if self._query_prefix else text
        q_emb = await self._embed_fn(query)
        if not q_emb:
            return GateDecision(generate=False, reason="no_embedding")
        q = list(q_emb)

        concrete, abstract = await _ANCHORS.get(self._embed_fn, self._query_prefix)
        if not concrete or not abstract:
            # Anchors unavailable — fail safe (no generation).
            return GateDecision(generate=False, reason="no_anchors")

        c = _max_sim(q, concrete, self._cosine)
        a = _max_sim(q, abstract, self._cosine)
        margin = c - a

        if c < self._concrete_floor:
            return GateDecision(False, "below_floor", round(c, 4), round(a, 4))
        if margin < self._margin:
            return GateDecision(False, "abstract", round(c, 4), round(a, 4))
        return GateDecision(True, "concrete", round(c, 4), round(a, 4))
