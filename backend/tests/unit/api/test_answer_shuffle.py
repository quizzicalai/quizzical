"""Unit tests for the deterministic answer-option shuffle in the quiz API.

AC-ANSWER-SHUFFLE-1: GET /quiz/status returns answer options in a
deterministic order seeded by (question_number, question_text). The same
question on a retry must yield the same order, but consecutive questions
should not always present option index 0 first.

AC-ANSWER-ROUNDTRIP-1 (2026-07-02, critical regression fix): the DISPLAYED
order (serve) and the RECORD order (de-map) must agree, so the option text the
server stores equals the option the user actually clicked. Before the fix the
serve path shuffled options while the record path indexed the raw stored order,
so ~75% of 4-option answers recorded a different option (introduced 2026-05-04).
"""
from __future__ import annotations

from app.agent.schemas import QuizQuestion
from app.api.endpoints.quiz import (
    _display_option_order,
    _format_next_question,
    _validate_and_record_answer,
)
from app.models.api import NextQuestionRequest


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


# ---------------------------------------------------------------------------
# AC-ANSWER-ROUNDTRIP-1 — serve→submit round trip must be faithful.
# ---------------------------------------------------------------------------
import uuid  # noqa: E402


def _state_with_one_question(q: QuizQuestion) -> dict:
    """Minimal GraphState-shaped dict for `_validate_and_record_answer`."""
    return {"quiz_history": [], "generated_questions": [q.model_dump()], "messages": []}


def test_display_order_reproduces_inplace_shuffle():
    """`_display_option_order` must reproduce the exact ordering `_format_next_question`
    applies, so serve and record agree by construction."""
    q = _make_question("Does the permutation match?", 3)
    served = [o.text for o in _format_next_question(q, question_number=4).options]
    order = _display_option_order(4, "Does the permutation match?", 4)
    original = ["opt-A-3", "opt-B-3", "opt-C-3", "opt-D-3"]
    assert served == [original[i] for i in order]


def test_answer_roundtrip_records_the_option_the_user_saw():
    """THE regression test: pick each displayed slot; the recorded answer_text
    must equal the text shown at that slot (not the raw stored order)."""
    q = _make_question("Which do you prefer, honestly?", 2)
    served = _format_next_question(q, question_number=1)
    displayed = [o.text for o in served.options]

    for displayed_slot, shown_text in enumerate(displayed):
        state = _state_with_one_question(q)
        req = NextQuestionRequest(
            quiz_id=uuid.uuid4(), question_index=0, option_index=displayed_slot
        )
        new_history, _ = _validate_and_record_answer(state, req)
        assert new_history[-1]["answer_text"] == shown_text, (
            f"slot {displayed_slot}: recorded {new_history[-1]['answer_text']!r} "
            f"but the user saw {shown_text!r}"
        )


def _first_reshuffling_question(n_opts: int = 4):
    """Find a question whose display permutation at question_number=1 (the value
    the record path uses for the first question) is NOT the identity, so the
    raw-vs-demapped divergence is observable. Returns (question, raw_texts,
    displayed_texts)."""
    for n in range(1, 50):
        raw = [f"opt-A-{n}", f"opt-B-{n}", f"opt-C-{n}", f"opt-D-{n}"][:n_opts]
        q = QuizQuestion(
            question_text=f"Reshuffle probe #{n}?", options=[{"text": t} for t in raw]
        )
        displayed = [o.text for o in _format_next_question(q, question_number=1).options]
        if displayed != raw:
            return q, raw, displayed
    raise AssertionError("no reshuffling question found (shuffle may be broken)")


def test_answer_roundtrip_exposes_the_raw_index_bug():
    """Proof the fix is load-bearing: for a question whose permutation is not the
    identity, naively indexing the RAW stored order (the pre-fix behaviour) would
    record a DIFFERENT option than the de-mapped (correct) path for at least one
    displayed slot. The record path uses question_number = q_index+1 = 1 for the
    first question, matching the serve number here."""
    q, raw, displayed = _first_reshuffling_question()
    mismatches = 0
    for slot, shown in enumerate(displayed):
        state = _state_with_one_question(q)
        req = NextQuestionRequest(quiz_id=uuid.uuid4(), question_index=0, option_index=slot)
        recorded = _validate_and_record_answer(state, req)[0][-1]["answer_text"]
        assert recorded == shown  # de-mapped path is correct
        if raw[slot] != shown:
            mismatches += 1  # the naive raw[slot] (pre-fix) would have been wrong here
    assert mismatches > 0, "expected at least one slot where the raw-index bug diverges"


def test_answer_roundtrip_stores_canonical_option_index():
    """The stored option_index is the canonical (original-order) index, consistent
    with answer_text — not the shuffled display index the client sent."""
    q, raw, displayed = _first_reshuffling_question()
    for slot, shown in enumerate(displayed):
        state = _state_with_one_question(q)
        req = NextQuestionRequest(quiz_id=uuid.uuid4(), question_index=0, option_index=slot)
        entry = _validate_and_record_answer(state, req)[0][-1]
        assert raw[entry["option_index"]] == shown
