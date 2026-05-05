"""Unit tests for the deterministic answer-option shuffle in the quiz API.

AC-ANSWER-SHUFFLE-1: GET /quiz/status returns answer options in a
deterministic order seeded by (question_number, question_text). The same
question on a retry must yield the same order, but consecutive questions
should not always present option index 0 first.
"""
from __future__ import annotations

from app.agent.schemas import QuizQuestion
from app.api.endpoints.quiz import _format_next_question


def _make_question(text: str, n: int) -> QuizQuestion:
    return QuizQuestion(
        question_text=text,
        options=[
            {"text": f"opt-A-{n}"},
            {"text": f"opt-B-{n}"},
            {"text": f"opt-C-{n}"},
            {"text": f"opt-D-{n}"},
        ],
    )


def test_shuffle_is_deterministic_for_same_question():
    """AC-ANSWER-SHUFFLE-1 — same (number, text) → identical order on every call."""
    q = _make_question("What is your favorite color?", 1)
    a = _format_next_question(q, question_number=1)
    b = _format_next_question(q, question_number=1)
    assert [o.text for o in a.options] == [o.text for o in b.options]


def test_shuffle_varies_across_questions():
    """Consecutive questions should not always start with the same source index.

    With 4 distinct (number, text) pairs we expect at least one of them to
    move the original first option ("opt-A-N") off slot 0.
    """
    first_slot_0_seen = []
    for n in range(1, 6):
        q = _make_question(f"Question number {n}?", n)
        out = _format_next_question(q, question_number=n)
        first_slot_0_seen.append(out.options[0].text)
    # Original "opt-A-N" should NOT be in slot 0 for every question.
    originals_in_slot_0 = sum(
        1 for n, t in enumerate(first_slot_0_seen, start=1) if t == f"opt-A-{n}"
    )
    assert originals_in_slot_0 < 5, (
        f"Shuffle never moved opt-A off slot 0: {first_slot_0_seen}"
    )


def test_shuffle_preserves_all_options():
    """Shuffling must not drop or duplicate options."""
    q = _make_question("Stable set test?", 7)
    out = _format_next_question(q, question_number=7)
    texts = sorted(o.text for o in out.options)
    assert texts == ["opt-A-7", "opt-B-7", "opt-C-7", "opt-D-7"]


def test_shuffle_skipped_for_single_option():
    """Don't waste cycles shuffling a single option (no-op)."""
    q = QuizQuestion(question_text="Only one?", options=[{"text": "only"}])
    out = _format_next_question(q, question_number=1)
    assert [o.text for o in out.options] == ["only"]
