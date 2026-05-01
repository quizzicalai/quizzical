"""§21 Phase 7 — batched evaluator/generator (`AC-PRECOMP-COST-2/3`)."""

from __future__ import annotations

import pytest

from app.services.precompute.batched import (
    BatchedScore,
    evaluate_batch,
    generate_baseline_questions,
)
from app.services.precompute.evaluator import EvaluatorResult


@pytest.mark.anyio
async def test_evaluator_one_call_for_all_artefacts():
    calls: list[int] = []

    async def _judge(items):
        calls.append(len(items))
        return [
            BatchedScore(
                artefact_id=str(i),
                result=EvaluatorResult(
                    score=8, blocking_reasons=(), non_blocking_notes=(),
                    sources=(), tier="cheap",
                ),
            )
            for i in range(len(items))
        ]

    out = await evaluate_batch(artefacts=list(range(6)), judge_fn=_judge)
    assert len(calls) == 1, "evaluator must be invoked exactly once"
    assert calls[0] == 6
    assert len(out) == 6


@pytest.mark.anyio
async def test_evaluator_empty_input_skips_call():
    calls: list[int] = []

    async def _judge(items):  # pragma: no cover — must not run
        calls.append(len(items))
        return []

    out = await evaluate_batch(artefacts=[], judge_fn=_judge)
    assert out == [] and calls == []


@pytest.mark.anyio
async def test_generator_one_call_for_baseline_set():
    calls: list[int] = []

    async def _gen(n: int):
        calls.append(n)
        return [{"q": f"q{i}"} for i in range(n)]

    out = await generate_baseline_questions(n=10, generate_fn=_gen)
    assert len(calls) == 1 and calls[0] == 10
    assert len(out) == 10
