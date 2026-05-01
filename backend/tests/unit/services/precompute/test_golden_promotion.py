"""§21 Phase 8 — golden-set promotion (`AC-PRECOMP-QUAL-3`)."""

from __future__ import annotations

import pytest

from app.services.precompute.golden import (
    GoldenItem,
    PrecisionRecall,
    evaluate_golden,
    strictly_improves,
)


def _items(*labels: bool) -> list[GoldenItem]:
    return [
        GoldenItem(artefact_id=str(i), artefact={"i": i}, label=lbl)
        for i, lbl in enumerate(labels)
    ]


@pytest.mark.anyio
async def test_evaluate_golden_basic_metrics():
    items = _items(True, True, False, False)
    # Predict: T, F, T, F → tp=1, fp=1, fn=1
    preds = iter([True, False, True, False])

    async def judge(_): return next(preds)

    pr = await evaluate_golden(items=items, judge_fn=judge)
    assert pr.precision == 0.5
    assert pr.recall == 0.5


def test_no_promote_without_strict_pr_improvement():
    """`AC-PRECOMP-QUAL-3` — equal precision OR equal recall blocks promotion."""
    incumbent = PrecisionRecall(precision=0.80, recall=0.70)

    # Same precision, better recall → blocked.
    cand = PrecisionRecall(precision=0.80, recall=0.75)
    assert not strictly_improves(candidate=cand, incumbent=incumbent)

    # Better precision, same recall → blocked.
    cand = PrecisionRecall(precision=0.85, recall=0.70)
    assert not strictly_improves(candidate=cand, incumbent=incumbent)

    # Worse on either dimension → blocked.
    cand = PrecisionRecall(precision=0.85, recall=0.69)
    assert not strictly_improves(candidate=cand, incumbent=incumbent)

    # BOTH strictly better → promoted.
    cand = PrecisionRecall(precision=0.81, recall=0.71)
    assert strictly_improves(candidate=cand, incumbent=incumbent)


@pytest.mark.anyio
async def test_evaluate_golden_empty_returns_zeros():
    pr = await evaluate_golden(items=[], judge_fn=lambda _: None)  # type: ignore[arg-type]
    assert pr.precision == 0.0 and pr.recall == 0.0
