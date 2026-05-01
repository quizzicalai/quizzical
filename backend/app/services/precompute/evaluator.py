"""§21 Phase 3 — evaluator service (`AC-PRECOMP-QUAL-2,5,6,7`).

Decouples scoring policy from the generator/LLM so a test can inject any
deterministic `JudgeFn`. The module owns the rules:

- Structured `EvaluatorResult` shape (`AC-PRECOMP-QUAL-5`):
  `{score, blocking_reasons[], non_blocking_notes[], sources?}`. Any
  blocking reason rejects regardless of score.

- Two-judge consensus on factual artefacts (`AC-PRECOMP-QUAL-2`): two
  independent judge runs whose minimum score is taken. A divergence > 2
  points triggers Tier-3 escalation by raising `EscalateToTier3`.

- Tier-3 source citation requirement (`AC-PRECOMP-QUAL-6`): when the
  judge runs at `tier="strong+search"`, every blocking reason MUST
  carry at least one `{url, snippet}` source; otherwise rejection.

- Cross-pack consistency (`AC-PRECOMP-QUAL-7`): a new character profile
  whose embedding diverges from the canonical version (cosine < 0.85) is
  rejected and the canonical character must be reused.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal

JudgeTier = Literal["cheap", "strong", "strong+search"]


@dataclass(frozen=True)
class Source:
    url: str
    snippet: str = ""


@dataclass(frozen=True)
class EvaluatorResult:
    """Structured judge output. `AC-PRECOMP-QUAL-5`."""

    score: int
    blocking_reasons: tuple[str, ...] = field(default_factory=tuple)
    non_blocking_notes: tuple[str, ...] = field(default_factory=tuple)
    sources: tuple[Source, ...] = field(default_factory=tuple)
    tier: JudgeTier = "cheap"

    @property
    def is_blocked(self) -> bool:
        return bool(self.blocking_reasons)


JudgeFn = Callable[..., Awaitable[EvaluatorResult]]


class EscalateToTier3(Exception):
    """`AC-PRECOMP-QUAL-2` — two-judge consensus diverged by > 2 points,
    forcing a Tier-3 (web-search) re-evaluation. The builder catches this
    and decides whether the cost guard allows escalation.
    """

    def __init__(self, scores: tuple[int, ...], reason: str = "two-judge-divergence") -> None:
        super().__init__(f"escalate to tier3: {reason} scores={scores}")
        self.scores = scores
        self.reason = reason


# ---------------------------------------------------------------------------
# Single-judge / two-judge gates
# ---------------------------------------------------------------------------


def assert_tier3_sources(result: EvaluatorResult) -> EvaluatorResult:
    """`AC-PRECOMP-QUAL-6` — Tier-3 blocking reasons MUST cite sources.

    A Tier-3 result whose `blocking_reasons` is non-empty but `sources`
    is empty is mutated into a Tier-3 result whose `score` is forced to
    0 with a synthetic blocking reason — callers therefore reject it
    without an extra branch.
    """
    if result.tier != "strong+search":
        return result
    if not result.blocking_reasons:
        return result
    if result.sources:
        return result
    return EvaluatorResult(
        score=0,
        blocking_reasons=tuple(result.blocking_reasons) + ("missing_sources",),
        non_blocking_notes=result.non_blocking_notes,
        sources=(),
        tier=result.tier,
    )


async def evaluate_single(
    *,
    judge_fn: JudgeFn,
    artefact: object,
    tier: JudgeTier = "cheap",
    pass_score: int,
    require_two_judge: bool = False,
    seed_a: int = 1,
    seed_b: int = 2,
    divergence_trigger: int = 2,
) -> EvaluatorResult:
    """Score `artefact` once (or twice for factual artefacts).

    Returns the (consensus) result. Raises `EscalateToTier3` when the
    two-judge divergence exceeds `divergence_trigger`.
    """

    a = await judge_fn(artefact=artefact, tier=tier, seed=seed_a)
    a = assert_tier3_sources(a)
    if not require_two_judge:
        return a

    b = await judge_fn(artefact=artefact, tier=tier, seed=seed_b)
    b = assert_tier3_sources(b)
    if abs(a.score - b.score) > divergence_trigger and tier != "strong+search":
        raise EscalateToTier3((a.score, b.score))

    # Take the minimum score and the union of blocking reasons / notes /
    # sources so a "lenient" judge cannot mask a "strict" one's findings.
    return EvaluatorResult(
        score=min(a.score, b.score),
        blocking_reasons=tuple(set(a.blocking_reasons) | set(b.blocking_reasons)),
        non_blocking_notes=tuple(set(a.non_blocking_notes) | set(b.non_blocking_notes)),
        sources=tuple({(s.url, s.snippet): s for s in (*a.sources, *b.sources)}.values()),
        tier=tier,
    )


def passes(result: EvaluatorResult, *, pass_score: int) -> bool:
    """Convenience predicate the builder calls after `evaluate_single`."""
    if result.is_blocked:
        return False
    return int(result.score) >= int(pass_score)


# ---------------------------------------------------------------------------
# Cross-pack consistency (`AC-PRECOMP-QUAL-7`)
# ---------------------------------------------------------------------------


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / ((na ** 0.5) * (nb ** 0.5))


CROSS_PACK_MIN_COSINE: float = 0.85


def is_cross_pack_consistent(
    *,
    new_embedding: list[float] | None,
    canonical_embedding: list[float] | None,
    threshold: float = CROSS_PACK_MIN_COSINE,
) -> bool:
    """`AC-PRECOMP-QUAL-7` — return True iff the new profile is "close
    enough" to the canonical version of the same character.

    Missing canonical (no prior pack) returns True — there is nothing to
    drift from yet. Missing new embedding returns False — we cannot
    verify, so we conservatively reject and let the builder re-embed.
    """
    if canonical_embedding is None:
        return True
    if new_embedding is None:
        return False
    return _cosine(list(new_embedding), list(canonical_embedding)) >= threshold
