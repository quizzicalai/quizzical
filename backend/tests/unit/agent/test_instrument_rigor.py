# backend/tests/unit/agent/test_instrument_rigor.py
"""INSTRUMENT RIGOR (owner blackbox #5, 2026-07-02) — module + data tests.

Covers the three layers that make validated-instrument topics (MBTI, DISC,
Big Five, Enneagram, Holland Codes) ask assessment-grade questions:

1. Catalog data: the rigorous instruments carry ``dimensions`` (what the
   instrument MEASURES) and the loader serves them via ``dimensions_for``.
2. The conditional block: present for instrument topics under any alias /
   noisy phrasing, EMPTY ("" — byte-for-byte no-op) for whimsical and
   canonical-but-casual topics.
3. Coverage bookkeeping: normalization of loose dimension labels onto
   canonical codes and least-covered targeting for the adaptive path.
"""

from __future__ import annotations

import pytest

from app.agent.canonical_sets import dimensions_for
from app.agent.instrument_rigor import (
    InstrumentSpec,
    instrument_spec_for,
    render_instrument_rigor_block,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Catalog data (data-only change)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "category,expected_codes",
    [
        ("Myers-Briggs Personality Types", ["E/I", "S/N", "T/F", "J/P"]),
        ("DISC Styles", ["D", "I", "S", "C"]),
        ("Big Five Personality Traits", ["O", "C", "E", "A", "N"]),
        (
            "Enneagram Types",
            [f"Type {i}" for i in range(1, 10)],
        ),
        ("Holland Codes", ["R", "I", "A", "S", "E", "C"]),
    ],
)
def test_rigorous_instruments_carry_dimensions(category, expected_codes):
    dims = dimensions_for(category)
    assert dims is not None, f"{category} must carry instrument dimensions"
    assert [d["code"] for d in dims] == expected_codes
    # Every dimension is fully described: a name and at least one pole.
    for d in dims:
        assert d["name"].strip()
        assert d["poles"], f"{category} dimension {d['code']} has no poles"


@pytest.mark.parametrize(
    "category",
    [
        "Hogwarts Houses",          # canonical but NOT an instrument
        "Western Zodiac Signs",     # canonical, astrology — no rigor dims
        "Attachment Styles",        # framework without dimensions configured
        "what type of troll am i",  # whimsical free text
        "Type of Coffee Drink",
        "",
        None,
    ],
)
def test_non_instruments_have_no_dimensions(category):
    assert dimensions_for(category) is None


def test_dimensions_for_returns_copies():
    """Mutating the returned dicts must not poison the compiled-config cache."""
    a = dimensions_for("mbti")
    a[0]["code"] = "MUTATED"
    b = dimensions_for("mbti")
    assert b[0]["code"] == "E/I"


# ---------------------------------------------------------------------------
# Spec resolution (aliases + noisy phrasings; App-Config overlay survives)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "phrase,expected_title",
    [
        ("mbti", "Myers-Briggs Personality Types"),
        ("16 personalities", "Myers-Briggs Personality Types"),
        # App-Config overlays MBTI/Big Five membership with dict entries that
        # do NOT carry dimensions — the merge must inherit the code-catalog
        # dimensions (the floor) or the rigor block silently dies in prod.
        ("Myers-Briggs Personality Types", "Myers-Briggs Personality Types"),
        ("big five", "Big Five Personality Traits"),
        ("What is my DISC type", "DISC Styles"),
        ("disc personality type", "DISC Styles"),
        ("enneagram", "Enneagram Types"),
        ("riasec", "Holland Codes"),
    ],
)
def test_instrument_spec_resolves_aliases(phrase, expected_title):
    spec = instrument_spec_for(phrase)
    assert spec is not None, f"{phrase!r} should resolve to an instrument"
    assert spec.title == expected_title


def test_instrument_spec_for_tries_candidates_in_order():
    spec = instrument_spec_for(None, "", "not an instrument", "mbti")
    assert spec is not None and spec.title == "Myers-Briggs Personality Types"
    assert instrument_spec_for(None, "", "still nothing") is None


# ---------------------------------------------------------------------------
# The conditional block: ON for instruments, byte-for-byte OFF otherwise
# ---------------------------------------------------------------------------


def test_block_present_for_mbti_with_required_directives():
    block = render_instrument_rigor_block("Myers-Briggs Personality Types")
    assert block.startswith("## INSTRUMENT RIGOR — Myers-Briggs Personality Types")
    # All four dichotomies listed with their codes.
    for code in ("E/I", "S/N", "T/F", "J/P"):
        assert f'"{code}"' in block
    # The rigor directives the owner asked for:
    assert "ONE dimension per question" in block
    assert "BALANCED coverage" in block
    assert "Behavioural/situational framing" in block
    assert "Neutral, non-leading wording" in block
    assert "astrology-style flattery" in block
    # And the output contract hook:
    assert '"dimension" field' in block


def test_block_absent_for_whimsical_topic():
    assert render_instrument_rigor_block("what type of troll am i") == ""


def test_block_absent_for_canonical_non_instrument():
    assert render_instrument_rigor_block("Hogwarts Houses") == ""


def test_block_ends_with_blank_line_for_clean_template_splice():
    block = render_instrument_rigor_block("disc")
    assert block.endswith("\n\n")


# ---------------------------------------------------------------------------
# Normalization of loose dimension labels
# ---------------------------------------------------------------------------


def _mbti() -> InstrumentSpec:
    spec = instrument_spec_for("mbti")
    assert spec is not None
    return spec


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("E/I", "E/I"),
        ("e/i", "E/I"),
        ("E-I", "E/I"),
        ("e i", "E/I"),
        ("Extraversion vs Introversion", "E/I"),
        ("J/P", "J/P"),
        ("judging vs perceiving", "J/P"),
        ("banana", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_code(raw, expected):
    assert _mbti().normalize_code(raw) == expected


# ---------------------------------------------------------------------------
# Coverage / least-covered targeting (the adaptive-path contract)
# ---------------------------------------------------------------------------


def test_coverage_counts_normalized_labels():
    spec = _mbti()
    counts = spec.coverage(["E/I", "e-i", "S/N", "nonsense"])
    assert counts == {"E/I": 2, "S/N": 1, "T/F": 0, "J/P": 0}


def test_under_covered_orders_by_canonical_order():
    spec = _mbti()
    assert spec.under_covered(["E/I", "E/I", "S/N"]) == ["T/F", "J/P"]
    # All-equal → every code is tied-lowest.
    assert spec.under_covered([]) == ["E/I", "S/N", "T/F", "J/P"]


def test_adaptive_block_names_the_least_covered_target():
    spec = _mbti()
    block = spec.render_question_block(asked_dimensions=["E/I", "E/I", "S/N"])
    assert "Coverage so far" in block
    assert "E/I: 2, S/N: 1, T/F: 0, J/P: 0" in block
    assert "UNDER-COVERED dimensions: T/F, J/P" in block
    assert 'MUST probe "T/F"' in block


def test_adaptive_block_handles_all_equal_coverage():
    spec = _mbti()
    block = spec.render_question_block(asked_dimensions=[])
    assert "equally covered" in block
    assert "MUST probe" not in block


def test_baseline_block_has_no_coverage_report():
    spec = _mbti()
    assert "Coverage so far" not in spec.render_question_block()


# ---------------------------------------------------------------------------
# Planner block
# ---------------------------------------------------------------------------


def test_plan_block_lists_dimensions_and_stays_measured():
    spec = instrument_spec_for("disc")
    block = spec.render_plan_block()
    assert block.startswith("## INSTRUMENT RIGOR — DISC Styles")
    for code in ("D (Dominance)", "I (Influence)", "S (Steadiness)", "C (Conscientiousness)"):
        assert code in block
    assert "astrology-style flattery" in block
    assert block.endswith("\n\n")
