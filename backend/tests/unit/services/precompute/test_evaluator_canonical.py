"""Tests for the evaluator's hard canonical-mismatch assertion.

`assert_canonical` augments an EvaluatorResult with a blocking
`canonical_mismatch` reason when the artefact's outcome set is wrong for a
canonical topic, so a high judge score can never promote a canonically-wrong
set. Blend-aware (DISC blend passes; wrong-named fails); non-canonical no-op.
"""

from __future__ import annotations

from app.services.precompute.canonical_gate import CANONICAL_MISMATCH_REASON
from app.services.precompute.evaluator import (
    EvaluatorResult,
    assert_canonical,
    passes,
)

HOGWARTS = ["Gryffindor", "Slytherin", "Ravenclaw", "Hufflepuff"]


def test_assert_canonical_blocks_single_mismatch_even_with_high_score() -> None:
    result = EvaluatorResult(score=10)  # judge loved it
    art = {"characters": [{"name": "Gryffindor"}, {"name": "Slytherin"}]}
    out = assert_canonical(result, category="Hogwarts Houses", artefact=art)
    assert out.is_blocked
    assert any(r.startswith(CANONICAL_MISMATCH_REASON) for r in out.blocking_reasons)
    assert passes(out, pass_score=7) is False


def test_assert_canonical_passes_single_exact_match() -> None:
    result = EvaluatorResult(score=8)
    art = {"characters": [{"name": n} for n in HOGWARTS]}
    out = assert_canonical(result, category="Hogwarts Houses", artefact=art)
    assert not out.is_blocked
    assert out is result  # unchanged identity on a pass


def test_assert_canonical_blended_disc_blend_passes() -> None:
    result = EvaluatorResult(score=8)
    art = {"characters": [{"name": "Dominance"}, {"name": "Influence"}]}
    out = assert_canonical(result, category="DISC", artefact=art)
    assert not out.is_blocked


def test_assert_canonical_blended_disc_wrong_named_blocks() -> None:
    result = EvaluatorResult(score=9)
    art = {"characters": [{"name": "Director"}, {"name": "Inspirer"}]}
    out = assert_canonical(result, category="DISC", artefact=art)
    assert out.is_blocked
    assert any(r.startswith(CANONICAL_MISMATCH_REASON) for r in out.blocking_reasons)


def test_assert_canonical_non_canonical_is_noop() -> None:
    result = EvaluatorResult(score=8, blocking_reasons=("some_other",))
    art = {"characters": [{"name": "Lover"}]}
    out = assert_canonical(result, category="Taylor Swift eras", artefact=art)
    assert out is result  # untouched


def test_assert_canonical_preserves_existing_reasons() -> None:
    result = EvaluatorResult(score=3, blocking_reasons=("score_below_threshold",))
    art = {"characters": [{"name": "Gryffindor"}]}
    out = assert_canonical(result, category="Hogwarts Houses", artefact=art)
    assert "score_below_threshold" in out.blocking_reasons
    assert any(r.startswith(CANONICAL_MISMATCH_REASON) for r in out.blocking_reasons)
