"""Tests for the blend-aware canonical persist-time gate.

Covers the comparison core (`compare_sets` / `check_artefact`):
  - canonical SINGLE: exact set match passes; missing/extra/renamed fails
  - canonical BLENDED (DISC): a palette-consistent blend passes (NOT forced
    one-of-N); a wrong-named (off-palette) set fails
  - non-canonical topics are a skip (no-op)
and the builder wiring (mismatch → REJECTED with `canonical_mismatch`, NOT
persisted; match → persisted).
"""

from __future__ import annotations

import pytest

from app.models.db import PrecomputeJob, Topic
from app.services.precompute import builder, jobs
from app.services.precompute.canonical_gate import (
    CANONICAL_MISMATCH_REASON,
    check_artefact,
    compare_sets,
)
from app.services.precompute.evaluator import EvaluatorResult
from tests.fixtures.db_fixtures import sqlite_db_session  # noqa: F401

pytestmark = pytest.mark.anyio


HOGWARTS = ["Gryffindor", "Slytherin", "Ravenclaw", "Hufflepuff"]
DISC = ["Dominance", "Influence", "Steadiness", "Conscientiousness"]


# ---------------------------------------------------------------------------
# Pure comparison core
# ---------------------------------------------------------------------------


def test_single_exact_match_passes() -> None:
    ok, diff = compare_sets(HOGWARTS, list(reversed(HOGWARTS)), outcome_mode="single")
    assert ok is True
    assert diff == ""


def test_single_case_and_accent_folded() -> None:
    ok, _ = compare_sets(HOGWARTS, ["gryffindor", "SLYTHERIN", "Ravenclaw", "hufflepuff"],
                         outcome_mode="single")
    assert ok is True


def test_single_missing_member_fails() -> None:
    ok, diff = compare_sets(HOGWARTS, HOGWARTS[:3], outcome_mode="single")
    assert ok is False
    assert "Hufflepuff" in diff


def test_single_extra_member_fails() -> None:
    ok, diff = compare_sets(HOGWARTS, [*HOGWARTS, "Durmstrang"], outcome_mode="single")
    assert ok is False
    assert "Durmstrang" in diff


def test_blended_full_set_passes() -> None:
    ok, diff = compare_sets(DISC, DISC, outcome_mode="blended")
    assert ok is True
    assert diff == ""


def test_blended_partial_blend_passes() -> None:
    # The load-bearing rule: a DISC blend (not all four, not exactly one) passes.
    ok, _ = compare_sets(DISC, ["Dominance", "Influence"], outcome_mode="blended")
    assert ok is True


def test_blended_off_palette_fails() -> None:
    ok, diff = compare_sets(DISC, ["Director", "Influence"], outcome_mode="blended")
    assert ok is False
    assert "Director" in diff


def test_empty_set_fails() -> None:
    ok, diff = compare_sets(HOGWARTS, [], outcome_mode="single")
    assert ok is False
    assert diff == "empty_outcome_set"


# ---------------------------------------------------------------------------
# check_artefact end-to-end against the live catalog
# ---------------------------------------------------------------------------


def test_check_artefact_canonical_single_ok() -> None:
    art = {"characters": [{"name": n} for n in HOGWARTS]}
    res = check_artefact("Hogwarts Houses", art)
    assert res.is_canonical and res.ok
    assert res.outcome_mode == "single"
    assert res.title == "Hogwarts Houses"


def test_check_artefact_canonical_single_mismatch() -> None:
    art = {"characters": [{"name": n} for n in ["Gryffindor", "Slytherin"]]}
    res = check_artefact("Hogwarts Houses", art)
    assert res.is_canonical and not res.ok


def test_check_artefact_blended_disc_partial_passes() -> None:
    art = {"characters": [{"name": "Dominance"}, {"name": "Influence"}]}
    res = check_artefact("DISC", art)
    assert res.outcome_mode == "blended"
    assert res.is_canonical and res.ok


def test_check_artefact_blended_disc_wrong_named_fails() -> None:
    art = {"characters": [{"name": "Director"}, {"name": "Inspirer"}]}
    res = check_artefact("DISC", art)
    assert res.is_canonical and not res.ok


def test_check_artefact_non_canonical_is_skip() -> None:
    res = check_artefact("Taylor Swift eras", {"characters": [{"name": "Lover"}]})
    assert res.is_canonical is False
    assert res.ok is True


def test_check_artefact_accepts_stored_character_set_strings() -> None:
    res = check_artefact("DISC", {"character_set": DISC})
    assert res.is_canonical and res.ok


# ---------------------------------------------------------------------------
# Builder wiring: reject-to-quarantine BEFORE persist
# ---------------------------------------------------------------------------


async def _seed(session, *, display_name: str, slug: str = "t") -> tuple[Topic, PrecomputeJob]:
    t = Topic(slug=slug, display_name=display_name, policy_status="allowed")
    session.add(t)
    await session.flush()
    j = await jobs.enqueue(session, topic_id=t.id)
    return t, j


async def test_builder_rejects_canonical_mismatch_without_persisting(sqlite_db_session) -> None:
    t, j = await _seed(sqlite_db_session, display_name="Hogwarts Houses")
    persisted: list[object] = []

    async def gen(topic, tier):
        # Wrong outcome set for a canonical (single) topic.
        return ({"characters": [{"name": "Gryffindor"}, {"name": "Slytherin"}]}, 5)

    async def ev(artefact, tier, pass_score, two_judge):
        return EvaluatorResult(score=9, tier=tier)  # judge LOVES it; gate must still reject

    async def persist(topic, artefact, result):
        persisted.append(artefact)

    out = await builder.run_build(
        sqlite_db_session, topic=t, job=j,
        generate_fn=gen, evaluate_fn=ev, persist_fn=persist,
        daily_budget_usd=5.0, default_pass_score=7,
    )
    await sqlite_db_session.commit()

    assert out.status == "rejected"
    assert CANONICAL_MISMATCH_REASON in out.rejection_reasons
    assert persisted == []  # NEVER persisted
    row = await sqlite_db_session.get(PrecomputeJob, j.id)
    assert row.status == "rejected"


async def test_builder_persists_canonical_match(sqlite_db_session) -> None:
    t, j = await _seed(sqlite_db_session, display_name="Hogwarts Houses")
    persisted: list[object] = []

    async def gen(topic, tier):
        return ({"characters": [{"name": n} for n in HOGWARTS]}, 5)

    async def ev(artefact, tier, pass_score, two_judge):
        return EvaluatorResult(score=8, tier=tier)

    async def persist(topic, artefact, result):
        persisted.append(artefact)

    out = await builder.run_build(
        sqlite_db_session, topic=t, job=j,
        generate_fn=gen, evaluate_fn=ev, persist_fn=persist,
        daily_budget_usd=5.0, default_pass_score=7,
    )
    await sqlite_db_session.commit()

    assert out.status == "succeeded"
    assert len(persisted) == 1


async def test_builder_persists_blended_disc_blend(sqlite_db_session) -> None:
    # The upcoming blended-DISC feature relies on the gate ALLOWING blends.
    t, j = await _seed(sqlite_db_session, display_name="DISC")
    persisted: list[object] = []

    async def gen(topic, tier):
        return ({"characters": [{"name": "Dominance"}, {"name": "Influence"}]}, 5)

    async def ev(artefact, tier, pass_score, two_judge):
        return EvaluatorResult(score=8, tier=tier)

    async def persist(topic, artefact, result):
        persisted.append(artefact)

    out = await builder.run_build(
        sqlite_db_session, topic=t, job=j,
        generate_fn=gen, evaluate_fn=ev, persist_fn=persist,
        daily_budget_usd=5.0, default_pass_score=7,
    )
    await sqlite_db_session.commit()
    assert out.status == "succeeded"
    assert len(persisted) == 1


async def test_builder_non_canonical_topic_skips_gate(sqlite_db_session) -> None:
    t, j = await _seed(sqlite_db_session, display_name="Taylor Swift eras")
    persisted: list[object] = []

    async def gen(topic, tier):
        return ({"characters": [{"name": "Lover"}, {"name": "Reputation"}]}, 5)

    async def ev(artefact, tier, pass_score, two_judge):
        return EvaluatorResult(score=8, tier=tier)

    async def persist(topic, artefact, result):
        persisted.append(artefact)

    out = await builder.run_build(
        sqlite_db_session, topic=t, job=j,
        generate_fn=gen, evaluate_fn=ev, persist_fn=persist,
        daily_budget_usd=5.0, default_pass_score=7,
    )
    await sqlite_db_session.commit()
    assert out.status == "succeeded"
    assert len(persisted) == 1
