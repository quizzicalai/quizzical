"""Unit tests for app.agent.progress_phrases.

These cover the small helper module that powers the new "confidence pill"
shown in the upper-right of the quiz UI in place of the old "X of Y" /
"% complete" indicators.
"""
from __future__ import annotations

import random

import pytest

from app.agent.progress_phrases import (
    ALL_NARROWING_PHRASES,
    BASELINE_PHRASES,
    MAX_PHRASE_LEN,
    band_for,
    baseline_phrase_for_index,
    pick_progress_phrase,
    pool_for_band,
    sanitize_phrase,
)


# ---------------------------------------------------------------------------
# baseline_phrase_for_index — AC-PROGRESS-PHRASE-2
# ---------------------------------------------------------------------------
class TestBaselinePhraseForIndex:
    def test_index_zero_returns_first_phrase(self) -> None:
        assert baseline_phrase_for_index(0) == BASELINE_PHRASES[0]

    def test_index_wraps_with_modulo(self) -> None:
        n = len(BASELINE_PHRASES)
        assert baseline_phrase_for_index(n) == BASELINE_PHRASES[0]
        assert baseline_phrase_for_index(n + 3) == BASELINE_PHRASES[3]

    def test_negative_index_clamped_to_zero(self) -> None:
        assert baseline_phrase_for_index(-5) == BASELINE_PHRASES[0]


# ---------------------------------------------------------------------------
# band_for — drives which pool the LLM/fallback picks from
# ---------------------------------------------------------------------------
class TestBandFor:
    @pytest.mark.parametrize(
        "confidence,answered,max_total,expected",
        [
            (0.0, 0, 10, "baseline"),
            (0.10, 0, 10, "baseline"),
            (0.25, 1, 20, "exploring"),
            (0.55, 5, 20, "narrowing"),
            (0.75, 10, 20, "closing"),
            (0.95, 18, 20, "imminent"),
        ],
    )
    def test_bands(self, confidence, answered, max_total, expected) -> None:
        assert band_for(confidence, answered=answered, max_total=max_total) == expected

    def test_progress_ratio_nudges_band_up(self) -> None:
        # Low confidence but most questions answered → still moves up.
        band = band_for(0.0, answered=18, max_total=20)
        # 18/20 * 0.85 = 0.765 → "closing"
        assert band == "closing"

    def test_invalid_confidence_falls_back_to_zero(self) -> None:
        assert band_for("nope", answered=0, max_total=10) == "baseline"  # type: ignore[arg-type]

    def test_clamps_above_one(self) -> None:
        assert band_for(5.0, answered=0, max_total=10) == "imminent"


# ---------------------------------------------------------------------------
# sanitize_phrase — AC-PROGRESS-PHRASE-3 (sanitization)
# ---------------------------------------------------------------------------
class TestSanitizePhrase:
    def test_returns_none_for_non_string(self) -> None:
        assert sanitize_phrase(None) is None
        assert sanitize_phrase(123) is None  # type: ignore[arg-type]

    def test_returns_none_for_empty_or_whitespace(self) -> None:
        assert sanitize_phrase("") is None
        assert sanitize_phrase("   \n\t") is None

    def test_collapses_whitespace(self) -> None:
        assert sanitize_phrase("  I'm   narrowing\nin…  ") == "I'm narrowing in…"

    def test_strips_surrounding_quotes(self) -> None:
        assert sanitize_phrase('"Almost there…"') == "Almost there…"
        assert sanitize_phrase("'Closing in…'") == "Closing in…"

    def test_length_cap_enforced(self) -> None:
        long = "x" * 200
        cleaned = sanitize_phrase(long)
        assert cleaned is not None
        assert len(cleaned) <= MAX_PHRASE_LEN

    def test_rejects_when_forbidden_term_present(self) -> None:
        # Case-insensitive substring match.
        assert (
            sanitize_phrase("Almost there, Gandalf…", forbidden_terms=["Gandalf"])
            is None
        )
        assert (
            sanitize_phrase("Closing in on FRODO", forbidden_terms=["frodo"])
            is None
        )

    def test_empty_forbidden_terms_are_ignored(self) -> None:
        assert sanitize_phrase("Closing in", forbidden_terms=["", "  "]) == "Closing in"


# ---------------------------------------------------------------------------
# pick_progress_phrase — fallback used when LLM omits / leaks
# ---------------------------------------------------------------------------
class TestPickProgressPhrase:
    def test_returns_value_from_correct_pool(self) -> None:
        rng = random.Random(0)
        phrase = pick_progress_phrase(
            confidence=0.95, answered=18, max_total=20, rng=rng
        )
        assert phrase in pool_for_band("imminent")

    def test_baseline_pool_used_for_zero_confidence(self) -> None:
        rng = random.Random(0)
        phrase = pick_progress_phrase(
            confidence=0.0, answered=0, max_total=10, rng=rng
        )
        assert phrase in BASELINE_PHRASES

    def test_phrase_respects_length_cap(self) -> None:
        for c in (0.0, 0.3, 0.6, 0.8, 1.0):
            for _ in range(5):
                phrase = pick_progress_phrase(
                    confidence=c, answered=1, max_total=10
                )
                assert len(phrase) <= MAX_PHRASE_LEN


# ---------------------------------------------------------------------------
# Pool integrity sanity checks
# ---------------------------------------------------------------------------
class TestPoolIntegrity:
    def test_all_pool_phrases_within_length_cap(self) -> None:
        for p in ALL_NARROWING_PHRASES + BASELINE_PHRASES:
            assert 1 <= len(p) <= MAX_PHRASE_LEN, p

    def test_no_pool_phrase_contains_markdown(self) -> None:
        forbidden_chars = set("*_`#[]<>")
        for p in ALL_NARROWING_PHRASES + BASELINE_PHRASES:
            assert not (set(p) & forbidden_chars), p
