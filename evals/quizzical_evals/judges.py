"""LLM-as-judge quality scoring, with bias controls.

Reuses the calibrated rubric from ``backend/Analysis/judge.py`` (a 1-5 Likert
scale across five quiz-specific dimensions) and generalises it so a single judge
call can score whichever dimension(s) a given function owns. The mapping from
agent function -> judge dimension is:

    initial_planner          -> synopsis_quality (+ character_completeness)
    profile_batch_writer     -> character_completeness
    profile_writer           -> character_completeness
    question_generator       -> baseline_quality, answer_option_quality
    next_question_generator  -> baseline_quality, answer_option_quality
    final_profile_writer     -> final_profile_quality
    decision_maker           -> (no judge; scored by deterministic checks +
                                 a calibration set, see methodology)

Bias mitigation (per 2026 LLM-judge best practice):
  * **Fixed judge model** held constant across all variants, so it is a *relative*
    yardstick. Swapping the judge is treated as an eval-suite migration.
  * **Self-preference guard**: never use a candidate model as its own judge. The
    default judge (``gpt-4o``) and the candidates (``gpt-4o-mini`` /
    ``gemini-flash``) are different model families; we additionally warn if a
    candidate family == judge family.
  * **Temperature 0** for the judge (reproducible scoring).
  * **Multi-judge ensemble** (optional): average scores from two judges of
    different families to damp any single judge's systematic bias.
  * **Reference-guided** scoring: when the input carries a canonical roster, it
    is passed to the judge as ground truth (reduces hallucinated grading).

This module's ``MockJudge`` lets the full pipeline run offline; it returns a
deterministic-but-clearly-synthetic score so reports are honestly ILLUSTRATIVE
until a live judge run replaces them.
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass, field

from .caller import Caller, LiveCaller

DIMENSIONS = (
    "synopsis_quality",
    "character_completeness",
    "baseline_quality",
    "answer_option_quality",
    "final_profile_quality",
)

# Which judge dimensions each agent function is responsible for.
FUNCTION_DIMENSIONS: dict[str, tuple[str, ...]] = {
    "initial_planner": ("synopsis_quality", "character_completeness"),
    "profile_batch_writer": ("character_completeness",),
    "profile_writer": ("character_completeness",),
    "question_generator": ("baseline_quality", "answer_option_quality"),
    "next_question_generator": ("baseline_quality", "answer_option_quality"),
    "final_profile_writer": ("final_profile_quality",),
}

JUDGE_SYSTEM = (
    "You are an exacting senior product reviewer for a BuzzFeed-style personality "
    "quiz generator. Score artifacts on a strict 1-5 Likert scale where:\n"
    "  1 = unusable (off-topic, broken, or misleading)\n"
    "  2 = weak (recognisable attempt with serious flaws)\n"
    "  3 = adequate (ships, but unremarkable)\n"
    "  4 = strong (clearly above average; minor nits)\n"
    "  5 = excellent (publishable as-is; on-tone and on-spec)\n"
    "Be calibrated: do not give 5s lightly, do not give 1s for merely mediocre work.\n"
    "Score ONLY the dimensions you are asked about. Return ONLY a JSON object."
)

# Per-dimension rubric text (verbatim from Analysis/judge.py so scores are
# comparable to the existing 108-run study).
RUBRIC = {
    "synopsis_quality": "correct topic framing, succinct, right tone for bucket.",
    "character_completeness": (
        "covers the right roster; for canonical buckets, matches canonical names; "
        "for open/media, covers a meaningful, non-trivial set."
    ),
    "baseline_quality": (
        "questions are diverse, well-targeted, non-leading, would form a fair "
        "posterior across characters."
    ),
    "answer_option_quality": (
        "options are meaningfully distinct AND map to different characters, but are "
        "NOT so transparent that a user can guess their way to a desired result; "
        "not vague either."
    ),
    "final_profile_quality": (
        "concrete, on-tone, references the simulated answers, feels custom to the winner."
    ),
}


@dataclass
class JudgeResult:
    scores: dict[str, float]
    agg: float
    rationale: str = ""
    judge_model: str = ""
    error: str | None = None
    per_judge: list[dict[str, float]] = field(default_factory=list)  # ensemble detail


def default_judge_model() -> str:
    """Strong, fixed judge. Prefer OpenAI gpt-4o; fall back to Gemini Pro."""
    if os.getenv("OPENAI_API_KEY"):
        return "gpt-4o-2024-11-20"
    return "gemini/gemini-2.5-pro"


def assert_not_self_judge(candidate_model: str, judge_model: str) -> None:
    """Warn (do not crash) if a candidate would be graded by its own family."""
    def fam(m: str) -> str:
        m = m.lower()
        return "openai" if m.startswith(("gpt-", "openai/", "o3", "o4")) else (
            "gemini" if m.startswith("gemini") else m.split("/")[0]
        )

    if fam(candidate_model) == fam(judge_model):
        import warnings

        warnings.warn(
            f"Self-preference risk: candidate '{candidate_model}' shares a family "
            f"with judge '{judge_model}'. Use a cross-family judge or an ensemble.",
            stacklevel=2,
        )


def build_judge_prompt(
    *,
    function: str,
    dimensions: tuple[str, ...],
    category: str,
    bucket: str,
    canonical: list[str] | None,
    artifact_preview: str,
) -> str:
    dims_block = "\n".join(
        f"{i+1}. {d} -- {RUBRIC[d]}" for i, d in enumerate(dimensions)
    )
    out_keys = ",\n".join(f'  "{d}": int' for d in dimensions)
    return (
        f"Score the output of the '{function}' step.\n\n"
        f"## Topic\nCategory: {category}\nBucket: {bucket}\n"
        f"Canonical roster (ground truth, if any): "
        f"{', '.join(canonical) if canonical else '(none)'}\n\n"
        f"## Artifact (JSON, may be partial)\n{artifact_preview}\n\n"
        f"## Rubric (score each 1-5)\n{dims_block}\n\n"
        f"Return ONLY this JSON:\n{{\n{out_keys},\n"
        f'  "rationale": string   // <= 50 words\n}}'
    )


def _coerce(scores: dict, dimensions: tuple[str, ...]) -> dict[str, float]:
    out: dict[str, float] = {}
    for d in dimensions:
        v = scores.get(d, 0)
        try:
            out[d] = max(0.0, min(5.0, float(v)))
        except Exception:
            out[d] = 0.0
    return out


async def judge_artifact(
    caller: Caller,
    *,
    function: str,
    dimensions: tuple[str, ...],
    category: str,
    bucket: str,
    canonical: list[str] | None,
    artifact_preview: str,
    judge_models: tuple[str, ...],
) -> JudgeResult:
    """Score one artifact with one or more judges (ensemble = mean of judges)."""
    user = build_judge_prompt(
        function=function,
        dimensions=dimensions,
        category=category,
        bucket=bucket,
        canonical=canonical,
        artifact_preview=artifact_preview,
    )
    per_judge: list[dict[str, float]] = []
    last_err: str | None = None
    for jm in judge_models:
        res = await caller.call_json(
            tool_name="judge",
            system=JUDGE_SYSTEM,
            user=user,
            model=jm,
            max_output_tokens=600,
            temperature=0.0,
            timeout_s=90,
        )
        if res.ok and isinstance(res.parsed, dict):
            per_judge.append(_coerce(res.parsed, dimensions))
        else:
            last_err = res.error or "non-dict judge response"
    if not per_judge:
        return JudgeResult(
            scores={d: 0.0 for d in dimensions}, agg=0.0,
            judge_model=",".join(judge_models), error=last_err,
        )
    # Ensemble = per-dimension mean across judges.
    scores = {
        d: sum(pj[d] for pj in per_judge) / len(per_judge) for d in dimensions
    }
    agg = sum(scores.values()) / len(scores) if scores else 0.0
    return JudgeResult(
        scores=scores, agg=agg, judge_model=",".join(judge_models),
        per_judge=per_judge,
    )


class MockJudge:
    """Offline judge: returns deterministic synthetic scores.

    Scores are seeded by (function, model) so the report has *structure* (some
    variants look better than others) without claiming to be real. The CLI marks
    any report built on MockJudge output as ILLUSTRATIVE.
    """

    _counter = 0

    async def call_json(self, *, model: str, user: str, **kwargs) -> object:
        from .caller import CallOutput
        from .pricing import Usage

        # Vary per call (not just per input) so reps differ -> CIs and paired
        # tests are non-degenerate in the ILLUSTRATIVE report. A real judge run
        # gets this variance for free from genuine scoring differences.
        MockJudge._counter += 1
        rng = random.Random(hash((model, user, MockJudge._counter)) & 0xFFFFFFFF)
        # Plausible illustrative priors loosely consistent with the prior 108-run
        # study: gemini-flash slightly ahead on creative dims, gpt-4o-mini close
        # behind, gpt-5-mini noisier/lower.
        base = 4.35 if "gemini" in model else (4.25 if "4o-mini" in model else 3.95)
        scores = {}
        for d in DIMENSIONS:
            scores[d] = round(min(5.0, max(1.0, rng.gauss(base, 0.4))), 2)
        scores["rationale"] = "ILLUSTRATIVE mock score (no live judge run)."
        return CallOutput(
            parsed=scores, raw_text="", usage=Usage(200, 80, 0),
            latency_wall_s=0.5, model=model, ok=True,
        )


def make_judge_caller(live: bool) -> Caller:
    return LiveCaller() if live else MockJudge()  # type: ignore[return-value]
