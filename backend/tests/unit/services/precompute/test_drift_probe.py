"""§21 Phase 8 — evaluator drift probe (`AC-PRECOMP-QUAL-4`)."""

from __future__ import annotations

import pytest

from app.jobs.evaluator_drift_probe import (
    DEFAULT_PAUSE_THRESHOLD_PP,
    run_drift_probe,
)


@pytest.mark.anyio
async def test_pass_rate_drop_pauses_evaluator():
    """`AC-PRECOMP-QUAL-4` — pass-rate drop > 10pp must set `paused=True`."""
    artefacts = list(range(10))
    # current pass rate: 5/10 = 0.5; baseline 0.9 → drift 40pp.

    async def judge(a):
        return a < 5

    rep = await run_drift_probe(
        artefacts=artefacts, judge_fn=judge, baseline_pass_rate=0.9,
    )
    assert rep.paused is True
    assert rep.drift_pp == pytest.approx(40.0)
    assert rep.sampled == 10


@pytest.mark.anyio
async def test_small_drop_within_threshold_does_not_pause():
    artefacts = list(range(10))
    # current 8/10 = 0.8; baseline 0.85 → drift 5pp ≤ 10.

    async def judge(a):
        return a < 8

    rep = await run_drift_probe(
        artefacts=artefacts, judge_fn=judge, baseline_pass_rate=0.85,
    )
    assert rep.paused is False
    assert rep.drift_pp == pytest.approx(5.0)


@pytest.mark.anyio
async def test_empty_sample_returns_inert_report():
    rep = await run_drift_probe(
        artefacts=[], judge_fn=lambda _: None, baseline_pass_rate=0.9,  # type: ignore[arg-type]
    )
    assert rep.sampled == 0 and rep.paused is False


def test_default_pause_threshold_documented():
    assert DEFAULT_PAUSE_THRESHOLD_PP == 10.0
