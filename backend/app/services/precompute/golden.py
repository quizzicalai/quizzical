"""§21 Phase 8 — golden-set promotion harness (`AC-PRECOMP-QUAL-3`).

A new evaluator (or new prompt version) may only be promoted to default
if its precision AND recall **both strictly improve** vs the incumbent on
the golden set. Equality on either dimension blocks promotion.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class GoldenItem:
    artefact_id: str
    artefact: object
    label: bool  # True = should pass evaluator; False = should fail


@dataclass(frozen=True)
class PrecisionRecall:
    precision: float
    recall: float


JudgeBool = Callable[[object], Awaitable[bool]]


async def evaluate_golden(
    *, items: Sequence[GoldenItem], judge_fn: JudgeBool
) -> PrecisionRecall:
    if not items:
        return PrecisionRecall(precision=0.0, recall=0.0)
    tp = fp = fn = 0
    for it in items:
        pred = bool(await judge_fn(it.artefact))
        if pred and it.label:
            tp += 1
        elif pred and not it.label:
            fp += 1
        elif not pred and it.label:
            fn += 1
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    return PrecisionRecall(precision=precision, recall=recall)


def strictly_improves(*, candidate: PrecisionRecall, incumbent: PrecisionRecall) -> bool:
    """`AC-PRECOMP-QUAL-3` — promotion requires BOTH precision and
    recall to strictly improve."""
    return (
        candidate.precision > incumbent.precision
        and candidate.recall > incumbent.recall
    )
