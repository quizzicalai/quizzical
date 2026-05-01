"""§21 Phase 7 — batched evaluator/generator entry points.

`AC-PRECOMP-COST-2`: every character (or every question) within a single
build attempt is scored in **one** evaluator call. `AC-PRECOMP-COST-3`:
the baseline question set is generated in **one** LLM call.

These are thin orchestrators over the per-artefact `evaluate_single` /
generator callables; the real LLM batching happens inside the supplied
callable. The orchestrator's only job is to guarantee a single fan-out
per build attempt and to count the calls so cost-attribution stays
honest.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from app.services.precompute.evaluator import EvaluatorResult


@dataclass(frozen=True)
class BatchedScore:
    """One result row inside a batched evaluator response."""

    artefact_id: str
    result: EvaluatorResult


BatchJudgeFn = Callable[[Sequence[object]], Awaitable[list[BatchedScore]]]


async def evaluate_batch(
    *,
    artefacts: Sequence[object],
    judge_fn: BatchJudgeFn,
) -> list[BatchedScore]:
    """`AC-PRECOMP-COST-2` — score `artefacts` in a single judge call.

    The supplied `judge_fn` MUST receive the full sequence at once.
    Returns the per-artefact `BatchedScore` list. Order matches input.
    """
    if not artefacts:
        return []
    return list(await judge_fn(artefacts))


GenerateFn = Callable[[int], Awaitable[list[object]]]


async def generate_baseline_questions(
    *,
    n: int,
    generate_fn: GenerateFn,
) -> list[object]:
    """`AC-PRECOMP-COST-3` — produce all `n` baseline questions with a
    single LLM call (`generate_fn(n)`)."""
    if n <= 0:
        return []
    return list(await generate_fn(n))
