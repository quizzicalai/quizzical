"""INSTRUMENT RIGOR eval plumbing (owner blackbox #5, 2026-07-02).

Covers the harness-side pieces of the rigor A/B:

* the ``no_rigor`` prompt strategy strips exactly the rigor hooks (placeholder
  + optional ``"dimension"`` output line) from the code default;
* the instrument deterministic checks (dimension validity, coverage balance,
  least-covered targeting) — including their inert pass-through for
  non-instrument cells;
* ``assemble_context`` supplies the production-shaped ``instrument_rigor``
  block for instrument records and "" otherwise.
"""
from __future__ import annotations

import pytest

from quizzical_evals.checks import run_checks
from quizzical_evals.datasets import assemble_context
from quizzical_evals.prompts_adapter import _ensure_backend_on_path, get_prompt_pair

backend_available = _ensure_backend_on_path()
needs_backend = pytest.mark.skipif(
    not backend_available, reason="backend not importable in this environment"
)


# ---------------------------------------------------------------------------
# no_rigor strategy
# ---------------------------------------------------------------------------


@needs_backend
@pytest.mark.parametrize(
    "function", ["initial_planner", "question_generator", "next_question_generator"]
)
def test_no_rigor_strips_placeholder_and_dimension_line(function: str) -> None:
    _, default_user = get_prompt_pair(function, "default")
    _, stripped_user = get_prompt_pair(function, "no_rigor")
    assert "{instrument_rigor}" in default_user
    assert "{instrument_rigor}" not in stripped_user
    assert '"dimension"' not in stripped_user
    # Everything else survives (the strip is surgical, not a rewrite).
    assert "{category}" in stripped_user


@needs_backend
def test_no_rigor_keeps_forbidden_block() -> None:
    for fn in ("question_generator", "next_question_generator"):
        _, user = get_prompt_pair(fn, "no_rigor")
        assert "ABSOLUTELY FORBIDDEN" in user, fn


# ---------------------------------------------------------------------------
# Deterministic checks
# ---------------------------------------------------------------------------

_MBTI_CTX = {"instrument_dimensions": ["E/I", "S/N", "T/F", "J/P"]}


def _q(dim: str | None) -> dict:
    q: dict = {"question_text": f"Q-{dim}", "options": [{"text": "A"}, {"text": "B"}]}
    if dim is not None:
        q["dimension"] = dim
    return q


def test_instrument_checks_pass_through_for_non_instrument_cells() -> None:
    out = {"questions": [_q(None), _q(None)]}
    res = run_checks(
        ["instrument_dimensions_valid", "instrument_coverage_balanced", "nqg_targets_under_covered"],
        out,
        {"instrument_dimensions": []},
    )
    assert all(res.values())


def test_instrument_dimensions_valid_requires_mappable_tags() -> None:
    good = {"questions": [_q("E/I"), _q("s-n")]}  # loose casing accepted
    bad = {"questions": [_q("E/I"), _q("banana")]}
    missing = {"questions": [_q("E/I"), _q(None)]}
    assert run_checks(["instrument_dimensions_valid"], good, _MBTI_CTX)["instrument_dimensions_valid"]
    assert not run_checks(["instrument_dimensions_valid"], bad, _MBTI_CTX)["instrument_dimensions_valid"]
    assert not run_checks(["instrument_dimensions_valid"], missing, _MBTI_CTX)["instrument_dimensions_valid"]


def test_instrument_coverage_balanced() -> None:
    # 6 questions over 4 dims: perfect = every dim >=1, none > ceil(6/4)=2.
    balanced = {"questions": [_q(d) for d in ["E/I", "E/I", "S/N", "T/F", "J/P", "S/N"]]}
    clustered = {"questions": [_q(d) for d in ["E/I", "E/I", "E/I", "S/N", "T/F", "J/P"]]}
    missing_dim = {"questions": [_q(d) for d in ["E/I", "E/I", "S/N", "S/N", "T/F", "T/F"]]}
    assert run_checks(["instrument_coverage_balanced"], balanced, _MBTI_CTX)["instrument_coverage_balanced"]
    assert not run_checks(["instrument_coverage_balanced"], clustered, _MBTI_CTX)["instrument_coverage_balanced"]
    assert not run_checks(["instrument_coverage_balanced"], missing_dim, _MBTI_CTX)["instrument_coverage_balanced"]
    # Fewer questions than dims: all distinct passes, duplicates fail.
    distinct = {"questions": [_q("E/I"), _q("S/N")]}
    dupes = {"questions": [_q("E/I"), _q("E/I")]}
    assert run_checks(["instrument_coverage_balanced"], distinct, _MBTI_CTX)["instrument_coverage_balanced"]
    assert not run_checks(["instrument_coverage_balanced"], dupes, _MBTI_CTX)["instrument_coverage_balanced"]


def test_nqg_targets_under_covered() -> None:
    ctx = dict(_MBTI_CTX, asked_dimensions=["E/I", "E/I", "S/N"])
    on_target = _q("T/F")
    also_ok = _q("J/P")
    off_target = _q("E/I")
    untagged = _q(None)
    assert run_checks(["nqg_targets_under_covered"], on_target, ctx)["nqg_targets_under_covered"]
    assert run_checks(["nqg_targets_under_covered"], also_ok, ctx)["nqg_targets_under_covered"]
    assert not run_checks(["nqg_targets_under_covered"], off_target, ctx)["nqg_targets_under_covered"]
    assert not run_checks(["nqg_targets_under_covered"], untagged, ctx)["nqg_targets_under_covered"]


# ---------------------------------------------------------------------------
# assemble_context: production-shaped instrument_rigor
# ---------------------------------------------------------------------------


@needs_backend
def test_assemble_context_fills_rigor_block_for_instrument_record() -> None:
    record = {
        "input_id": "mbti",
        "category": "Myers-Briggs Personality Types",
        "bucket": "instrument",
        "instrument_dimensions": ["E/I", "S/N", "T/F", "J/P"],
        "asked_dimensions": ["E/I", "E/I", "S/N"],
    }
    qg_ctx = assemble_context("question_generator", record)
    assert "INSTRUMENT RIGOR — Myers-Briggs Personality Types" in qg_ctx["instrument_rigor"]
    assert "Coverage so far" not in qg_ctx["instrument_rigor"]  # baseline: no report

    nqg_ctx = assemble_context("next_question_generator", record)
    assert "Coverage so far" in nqg_ctx["instrument_rigor"]
    assert 'MUST probe "T/F"' in nqg_ctx["instrument_rigor"]

    # Check-facing raw fields survive into ctx.
    assert nqg_ctx["instrument_dimensions"] == ["E/I", "S/N", "T/F", "J/P"]
    assert nqg_ctx["asked_dimensions"] == ["E/I", "E/I", "S/N"]


@needs_backend
def test_assemble_context_empty_rigor_for_non_instrument_record() -> None:
    record = {"input_id": "troll", "category": "what type of troll am i", "bucket": "open"}
    ctx = assemble_context("question_generator", record)
    assert ctx["instrument_rigor"] == ""
    assert ctx["instrument_dimensions"] == []
