"""§21 Phase 3 — evaluator gate tests.

Covers:
  - AC-PRECOMP-QUAL-2 (two-judge divergence > 2 escalates)
  - AC-PRECOMP-QUAL-5 (structured output: blocking_reasons override score)
  - AC-PRECOMP-QUAL-6 (Tier-3 reasons require sources)
  - AC-PRECOMP-QUAL-7 (cross-pack consistency cosine threshold)
"""

from __future__ import annotations

import pytest

from app.services.precompute.evaluator import (
    EscalateToTier3,
    EvaluatorResult,
    Source,
    assert_tier3_sources,
    evaluate_single,
    is_cross_pack_consistent,
    passes,
)

pytestmark = pytest.mark.anyio


def _r(score: int, *, tier: str = "cheap", reasons=(), sources=()) -> EvaluatorResult:
    return EvaluatorResult(
        score=score, tier=tier, blocking_reasons=tuple(reasons),
        sources=tuple(sources),
    )


def test_blocking_reasons_force_failure_regardless_of_score() -> None:
    r = _r(10, reasons=("nsfw",))
    assert r.is_blocked is True
    assert passes(r, pass_score=7) is False


def test_passes_uses_score_threshold_when_unblocked() -> None:
    assert passes(_r(7), pass_score=7) is True
    assert passes(_r(6), pass_score=7) is False


def test_tier3_blocking_without_sources_is_rejected_synthetically() -> None:
    r = _r(8, tier="strong+search", reasons=("hallucination",), sources=())
    coerced = assert_tier3_sources(r)
    assert coerced.is_blocked is True
    assert coerced.score == 0
    assert "missing_sources" in coerced.blocking_reasons


def test_tier3_blocking_with_sources_is_left_alone() -> None:
    r = _r(8, tier="strong+search", reasons=("hallucination",),
            sources=(Source(url="https://x"),))
    coerced = assert_tier3_sources(r)
    assert coerced is r  # unchanged


async def test_two_judge_divergence_escalates() -> None:
    # First judge scores 9, second scores 5 → divergence = 4 > 2.
    seq = iter([_r(9), _r(5)])

    async def judge_fn(*, artefact, tier, seed):
        return next(seq)

    with pytest.raises(EscalateToTier3) as exc:
        await evaluate_single(
            judge_fn=judge_fn, artefact=object(), tier="strong",
            pass_score=7, require_two_judge=True,
        )
    assert exc.value.scores == (9, 5)


async def test_two_judge_consensus_takes_minimum_score() -> None:
    seq = iter([_r(9), _r(8)])

    async def judge_fn(*, artefact, tier, seed):
        return next(seq)

    out = await evaluate_single(
        judge_fn=judge_fn, artefact=object(), tier="strong",
        pass_score=7, require_two_judge=True,
    )
    assert out.score == 8
    assert out.is_blocked is False


def test_cross_pack_consistency_threshold() -> None:
    a = [1.0, 0.0, 0.0]
    b = [0.95, 0.05, 0.0]
    c = [0.0, 1.0, 0.0]
    assert is_cross_pack_consistent(new_embedding=a, canonical_embedding=b) is True
    assert is_cross_pack_consistent(new_embedding=a, canonical_embedding=c) is False
    assert is_cross_pack_consistent(new_embedding=a, canonical_embedding=None) is True
    assert is_cross_pack_consistent(new_embedding=None, canonical_embedding=a) is False
