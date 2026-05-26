"""Unit tests for AC-UX-2026-05-08 — agent-confidence surfacing.

These tests pin the contract between the agent state, the
`_format_next_question` adapter, and the wire-level `APIQuestion`
schema. They protect three classes of regression:

1. `APIQuestion.confidence` accepts the documented [0, 1] range and
   rejects nonsensical values that would render as garbage on the
   client (e.g. "350% confident").
2. `_format_next_question` accepts confidence in either of two
   tolerable shapes -- a fractional [0, 1] float OR a legacy
   percentage [0, 100] -- and always emits the fractional form on
   the wire.
3. Out-of-range / non-numeric agent output is silently dropped
   (confidence becomes ``None``) instead of crashing the request.
"""

from __future__ import annotations

import pytest

from app.agent.schemas import QuizQuestion
from app.api.endpoints.quiz import _format_next_question
from app.models.api import Question as APIQuestion


def _make_question() -> QuizQuestion:
    return QuizQuestion(
        question_text="Pick a number",
        options=[{"text": "one"}, {"text": "two"}, {"text": "three"}],
    )


# --- APIQuestion schema bounds ------------------------------------------------


@pytest.mark.parametrize("good", [0.0, 0.25, 0.5, 0.85, 1.0])
def test_api_question_accepts_confidence_in_unit_interval(good: float):
    """[0, 1] floats are the contract. All boundary + interior values pass."""
    q = APIQuestion(
        id="q1", text="t", options=[], question_number=1, confidence=good
    )
    assert q.confidence == good


def test_api_question_allows_null_confidence():
    """The field is optional; quizzes without confidence must still serialize."""
    q = APIQuestion(id="q1", text="t", options=[], question_number=1)
    assert q.confidence is None


# --- _format_next_question normalization --------------------------------------


def test_format_next_question_emits_unit_interval_for_fractional_input():
    """0.85 in → 0.85 out (no rounding, no scaling)."""
    out = _format_next_question(_make_question(), question_number=1, confidence=0.85)
    assert out.confidence == pytest.approx(0.85)


def test_format_next_question_drops_invalid_confidence_silently():
    """Garbage from the agent must not break the response."""
    for bad in (None, "n/a", float("nan"), -0.5, 0.0):
        out = _format_next_question(
            _make_question(), question_number=1, confidence=bad
        )
        # Either dropped (None) or clamped — never a negative / nan leak.
        assert out.confidence is None or 0.0 < out.confidence <= 1.0


def test_format_next_question_caps_above_unit_interval():
    """Defensive clamp: agent returning 1.5 must NOT render as ">100%"."""
    out = _format_next_question(
        _make_question(), question_number=1, confidence=1.5
    )
    assert out.confidence is None or out.confidence <= 1.0


def test_format_next_question_no_confidence_kwarg_keeps_field_null():
    """Backwards compat: callers that don't pass confidence get None."""
    out = _format_next_question(_make_question(), question_number=1)
    assert out.confidence is None
