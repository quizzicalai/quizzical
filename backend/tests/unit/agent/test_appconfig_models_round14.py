# backend/tests/unit/agent/test_appconfig_models_round14.py
"""Round 14 regression tests — 2026-07-02 agent-tuning eval outcomes.

A live eval (reps=20, judge gemini-2.5-pro) re-decided the four generative
functions after the fidelity/grounding fixes. All four KEEP gpt-4o-mini; the
notable structural change is that the CoT-styled App-Config prompt overrides for
question_generator / next_question_generator were REMOVED (they scored worse
than the code default for gpt-4o-mini). These tests pin the resulting config so
a future edit can't silently regress it.

Numbers (mean judge quality 1-5, 100%-valid unless noted):
- profile_batch_writer  gpt-4o-mini 2.20  (flash-latest 2.07 @ 11x cost; flash-lite 1.77 @ 56% valid)
- final_profile_writer  gpt-4o-mini 4.68  (ONLY variant clearing the 4.2 floor; gpt-5-mini 4.85 @ 68% valid)
- question_generator    gpt-4o-mini 3.66 (code default) vs 3.41 (override); no gpt-4o-mini variant clears 4.0
- next_question_generator gpt-4o-mini 4.45 (code default, clears 4.0) vs 4.36 override @ 2.75x p95

Report: evals/REPORT-TUNING-2026-07-02.md.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]


def _cfg() -> dict:
    cfg_path = Path(__file__).resolve().parents[3] / "appconfig.local.yaml"
    with cfg_path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _tools() -> dict:
    return _cfg()["quizzical"]["llm"]["tools"]


@pytest.mark.parametrize(
    "fn",
    [
        "profile_batch_writer",
        "final_profile_writer",
        "question_generator",
        "next_question_generator",
    ],
)
def test_all_four_eval_functions_use_gpt4o_mini(fn: str) -> None:
    """AC-EVAL-2026-07-02: gpt-4o-mini is the cost→quality→perf winner (or only
    floor-clearer) for every function evaluated this round."""
    model = _tools()[fn]["model"]
    assert model == "gpt-4o-mini", (
        f"{fn} must use gpt-4o-mini per the 2026-07-02 eval. Got {model!r}."
    )


def test_final_profile_writer_cleared_its_strict_floor() -> None:
    """final_profile_writer was the strictest (4.2) floor and had never been
    live-validated; gpt-4o-mini is the ONLY variant that cleared it (4.68,
    CI-lower 4.57), 100% valid. Keep a sane visible-token cap."""
    fpw = _tools()["final_profile_writer"]
    assert fpw["model"] == "gpt-4o-mini"
    assert fpw["max_output_tokens"] <= 2000


def test_qg_nqg_prompt_overrides_removed() -> None:
    """AC-EVAL-2026-07-02: the CoT-styled QG/NQG prompt overrides were removed
    (worse than the code default for gpt-4o-mini). Both resolve to
    DEFAULT_PROMPTS now — which still carries the anti-self-match FORBIDDEN
    block (asserted in test_appconfig_prompts_forbidden.py)."""
    prompts = _cfg()["quizzical"]["llm"].get("prompts", {}) or {}
    assert "question_generator" not in prompts
    assert "next_question_generator" not in prompts
