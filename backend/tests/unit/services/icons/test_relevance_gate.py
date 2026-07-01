"""Relevance-gate unit tests (PRIORITY 2 — the make-or-break budget guardrail).

Uses a deterministic FAKE embedder (no fastembed / no model download) so the
gate's decision logic — pre-filters, margin/floor thresholds, fail-safe — is
exercised in CI without the heavy model. The REAL-embedder routing quality is
validated separately by the offline eval (``specifications/prototype/
qa_relevance_eval.py`` → ``qa_relevance_eval.json``).
"""

from __future__ import annotations

import pytest

from app.services.icons.relevance_gate import (
    _ANCHORS,
    ABSTRACT_ANCHORS,
    CONCRETE_ANCHORS,
    GateDecision,
    QuestionGateDecision,
    RelevanceGate,
)

pytestmark = pytest.mark.anyio


def _fake_embedder(concrete_words: set[str]):
    """Return an async embed_fn over a tiny 3-dim space.

    Anchors map to one of two poles; a query leans concrete iff it contains a
    word in ``concrete_words``. Vectors are crafted so cosine cleanly separates
    the poles — enough to exercise margin/floor without a real model.
    """
    CONCRETE_VEC = [1.0, 0.0, 0.0]
    ABSTRACT_VEC = [0.0, 1.0, 0.0]
    NEUTRAL_VEC = [0.5, 0.5, 0.0]

    async def embed(text: str):
        if not text or not text.strip():
            return None
        low = text.lower()
        if text in CONCRETE_ANCHORS:
            return list(CONCRETE_VEC)
        if text in ABSTRACT_ANCHORS:
            return list(ABSTRACT_VEC)
        # Strip the query prefix when present.
        if any(w in low for w in concrete_words):
            return list(CONCRETE_VEC)
        return list(NEUTRAL_VEC)

    return embed


@pytest.fixture(autouse=True)
def _reset_anchor_cache():
    # The anchor cache is process-wide and keyed on the embed_fn identity; reset
    # it between tests so each test's fake embedder recomputes its own anchors.
    _ANCHORS._concrete = None
    _ANCHORS._abstract = None
    _ANCHORS._key = None
    yield
    _ANCHORS._concrete = None
    _ANCHORS._abstract = None
    _ANCHORS._key = None


async def test_concrete_string_routes_to_generation():
    gate = RelevanceGate(
        embed_fn=_fake_embedder({"dragon"}),
        margin=0.04,
        concrete_floor=0.20,
    )
    d = await gate.score("A fierce dragon over a mountain")
    assert d.generate is True
    assert d.reason == "concrete"
    assert d.concrete_sim > d.abstract_sim


async def test_abstract_string_falls_back():
    # No concrete word => query sits at the neutral midpoint => zero margin =>
    # below the 0.04 margin threshold => no generation (falls back to icon).
    gate = RelevanceGate(
        embed_fn=_fake_embedder({"dragon"}),
        margin=0.04,
        concrete_floor=0.20,
    )
    d = await gate.score("How do you feel about taking risks?")
    assert d.generate is False
    assert d.reason in {"abstract", "below_floor"}


async def test_template_answers_skipped_without_embedding():
    calls = {"n": 0}

    async def counting_embed(text: str):
        calls["n"] += 1
        return [1.0, 0.0, 0.0]

    gate = RelevanceGate(embed_fn=counting_embed)
    for t in ("None of the above", "It depends.", "Other", "n/a"):
        d = await gate.score(t)
        assert d.generate is False
        assert d.reason == "template"
    # Pre-filter short-circuits BEFORE any embed call.
    assert calls["n"] == 0


async def test_blank_and_too_short_skipped():
    gate = RelevanceGate(embed_fn=_fake_embedder({"x"}))
    assert (await gate.score("")).reason == "blank"
    assert (await gate.score("   ")).reason == "blank"
    assert (await gate.score("hi")).reason == "too_short"


async def test_gate_fails_safe_on_embedder_error():
    async def boom(text: str):
        raise RuntimeError("embedder down")

    gate = RelevanceGate(embed_fn=boom)
    d = await gate.score("A concrete depictable castle on a hill")
    # Any error => no generation (fail SAFE: never spend on a broken signal).
    assert d.generate is False
    assert d.reason == "error"


async def test_margin_property_is_concrete_minus_abstract():
    d = GateDecision(generate=True, reason="concrete", concrete_sim=0.6, abstract_sim=0.5)
    assert d.margin == pytest.approx(0.1, abs=1e-6)


# ---------------------------------------------------------------------------
# Blackbox #5 — PER-QUESTION all-or-none gate (score_question).
# ---------------------------------------------------------------------------

async def test_score_question_clears_when_majority_concrete():
    """A question whose answer SET leans concrete clears as a unit (default
    question_min_fraction=0.5)."""
    gate = RelevanceGate(
        embed_fn=_fake_embedder({"dragon", "castle"}),
        margin=0.04,
        concrete_floor=0.20,
        question_min_fraction=0.5,
    )
    qd = await gate.score_question(
        [
            "A fierce dragon over a mountain",  # concrete
            "A castle on a cliff",  # concrete
            "How you feel about risk",  # abstract
        ]
    )
    assert isinstance(qd, QuestionGateDecision)
    assert qd.generate is True
    assert qd.n_answers == 3
    assert qd.n_concrete_answers == 2
    assert qd.concrete_fraction == pytest.approx(0.6667, abs=1e-3)


async def test_score_question_blocks_when_mostly_abstract():
    """A question whose answers are mostly abstract is routed away as a UNIT."""
    gate = RelevanceGate(
        embed_fn=_fake_embedder({"dragon"}),
        margin=0.04,
        concrete_floor=0.20,
        question_min_fraction=0.5,
    )
    qd = await gate.score_question(
        [
            "A fierce dragon over a mountain",  # concrete (1/3)
            "Quietly confident and reserved",  # abstract
            "Calm and patient in a crisis",  # abstract
        ]
    )
    assert qd.generate is False
    assert qd.n_concrete_answers == 1
    assert qd.concrete_fraction == pytest.approx(0.3333, abs=1e-3)


async def test_score_question_empty_is_no_generation():
    gate = RelevanceGate(embed_fn=_fake_embedder({"dragon"}))
    qd = await gate.score_question([])
    assert qd.generate is False
    assert qd.reason == "no_answers"


async def test_score_question_fails_safe_on_error():
    async def boom(text: str):
        raise RuntimeError("embedder down")

    gate = RelevanceGate(embed_fn=boom)
    qd = await gate.score_question(["A castle", "A dragon"])
    # An embedder error inside per-answer scoring is swallowed (score() returns a
    # no-generation decision), so the fraction is 0 and the question is blocked.
    assert qd.generate is False
