"""§21 Phase 8 — weekly evaluator drift probe (`AC-PRECOMP-QUAL-4`).

Re-evaluates a 5% sample of recently-built artefacts. If the pass-rate
drops by more than `pause_threshold_pp` percentage points vs the recorded
baseline, sets `evaluator_drift_paused=True` and returns a `DriftReport`
suitable for alerting.

`pause_threshold_pp` defaults to 10 (i.e. 10 percentage points)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

DEFAULT_PAUSE_THRESHOLD_PP: float = 10.0


@dataclass(frozen=True)
class DriftReport:
    sampled: int
    current_pass_rate: float
    baseline_pass_rate: float
    drift_pp: float  # baseline - current, in percentage points
    paused: bool


JudgeBool = Callable[[object], Awaitable[bool]]


async def run_drift_probe(
    *,
    artefacts: Sequence[object],
    judge_fn: JudgeBool,
    baseline_pass_rate: float,
    pause_threshold_pp: float = DEFAULT_PAUSE_THRESHOLD_PP,
) -> DriftReport:
    if not artefacts:
        return DriftReport(
            sampled=0, current_pass_rate=0.0,
            baseline_pass_rate=baseline_pass_rate, drift_pp=0.0, paused=False,
        )
    passes = 0
    for a in artefacts:
        if await judge_fn(a):
            passes += 1
    current = passes / len(artefacts)
    drift_pp = (baseline_pass_rate - current) * 100.0
    paused = drift_pp > pause_threshold_pp
    return DriftReport(
        sampled=len(artefacts),
        current_pass_rate=current,
        baseline_pass_rate=baseline_pass_rate,
        drift_pp=drift_pp,
        paused=paused,
    )
