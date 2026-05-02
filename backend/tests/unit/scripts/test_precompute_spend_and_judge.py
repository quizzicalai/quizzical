"""Unit tests for the spend ledger and topic-pool loader."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts._precompute_spend import (
    COST_FAL_IMAGE_CENTS,
    COST_LLM_JUDGE_CALL_CENTS,
    COST_LLM_TEXT_CALL_CENTS,
    SpendLedger,
    estimate_topic_image_cost_cents,
    estimate_topic_judge_cost_cents,
    estimate_topic_text_cost_cents,
)


def test_spend_ledger_charges_distinct_kinds():
    led = SpendLedger(cap_cents=1000)
    led.charge_llm_text(4)
    led.charge_llm_judge(2)
    led.charge_fal_image(5)
    expected = (
        4 * COST_LLM_TEXT_CALL_CENTS
        + 2 * COST_LLM_JUDGE_CALL_CENTS
        + 5 * COST_FAL_IMAGE_CENTS
    )
    assert led.spent_cents == pytest.approx(expected)
    assert led.operations == {"llm_text": 4, "llm_judge": 2, "fal_image": 5}


def test_spend_ledger_would_exceed_respects_disabled_cap():
    led = SpendLedger(cap_cents=0)
    assert led.would_exceed(1_000_000) is False


def test_spend_ledger_would_exceed_blocks_when_above_cap():
    led = SpendLedger(cap_cents=100)
    led.charge_llm_text(100)  # 100 * 0.5 = 50 cents
    assert led.would_exceed(60) is True
    assert led.would_exceed(40) is False


def test_estimate_helpers_match_constants():
    assert estimate_topic_text_cost_cents() == pytest.approx(4 * COST_LLM_TEXT_CALL_CENTS)
    assert estimate_topic_judge_cost_cents() == pytest.approx(2 * COST_LLM_JUDGE_CALL_CENTS)
    assert estimate_topic_image_cost_cents(6) == pytest.approx(6 * COST_FAL_IMAGE_CENTS)
    assert estimate_topic_image_cost_cents(0) == pytest.approx(0.0)


def test_topic_pool_loader_skips_blank_entries(tmp_path: Path):
    from scripts.generate_ranked_pack_candidates import _load_topic_pool

    pool_path = tmp_path / "pool.json"
    pool_path.write_text(
        json.dumps(
            [
                {"slug": "starter-pokemon", "display_name": "Starter Pokémon", "rationale": "iconic"},
                {"slug": "", "display_name": "missing slug"},
                {"slug": "no-display"},  # missing display_name
                {"slug": "Marvel-Avengers", "display_name": "Marvel Avengers"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    loaded = _load_topic_pool(pool_path)
    assert [c.slug for c in loaded] == ["starter-pokemon", "marvel-avengers"]
    assert loaded[0].source == "llm_pool"
    # source_rank preserves the original pool position so operators can
    # correlate gaps caused by malformed entries.
    assert loaded[0].source_rank == 1
    assert loaded[1].source_rank == 4
    assert loaded[0].selection_reason == "iconic"


@pytest.mark.asyncio
async def test_run_judge_returns_disabled_metadata_when_no_judge_fn():
    from scripts.generate_ranked_pack_candidates import _run_judge

    out = await _run_judge(topic={}, judge_fn=None, pass_score=75, spend_ledger=None)
    assert out == {"judge_enabled": False}


@pytest.mark.asyncio
async def test_run_judge_records_two_calls_and_passes(monkeypatch):
    from app.services.precompute.evaluator import EvaluatorResult
    from scripts.generate_ranked_pack_candidates import _run_judge

    calls = {"n": 0}

    async def fake_judge(*, artefact, tier, seed):
        calls["n"] += 1
        return EvaluatorResult(score=90, tier=tier)

    led = SpendLedger(cap_cents=10_000)
    out = await _run_judge(
        topic={"slug": "x"},
        judge_fn=fake_judge,
        pass_score=75,
        spend_ledger=led,
    )
    assert calls["n"] == 2
    assert out["judge_enabled"] is True
    assert out["judge_passed"] is True
    assert out["judge_score"] == 90
    assert led.operations == {"llm_judge": 2}


@pytest.mark.asyncio
async def test_run_judge_handles_divergence_escalation(monkeypatch):
    from app.services.precompute.evaluator import EvaluatorResult
    from scripts.generate_ranked_pack_candidates import _run_judge

    scores = iter([95, 50])  # divergence > 2 triggers EscalateToTier3

    async def fake_judge(*, artefact, tier, seed):
        return EvaluatorResult(score=next(scores), tier=tier)

    led = SpendLedger(cap_cents=10_000)
    out = await _run_judge(
        topic={"slug": "x"},
        judge_fn=fake_judge,
        pass_score=75,
        spend_ledger=led,
    )
    assert out["judge_enabled"] is True
    assert out["judge_passed"] is False
    assert "two_judge_divergence" in out["judge_blocking_reasons"]
    assert led.operations == {"llm_judge": 2}
