# backend/tests/unit/agent/test_appconfig_models_round13.py
"""Round 13 regression tests for production model selection (eval-driven swap).

Spec refs:
- AC-PROD-R13-PERF-1 — `profile_batch_writer` must use `gpt-4o-mini` (was
  `gemini/gemini-flash-latest`). It was the single most expensive function
  ($8.15/1k on flash-latest vs $0.61/1k on gpt-4o-mini, ~13x cheaper) AND
  scored higher judged quality. Gemini's hidden-CoT reasoning tax is gone, so
  the output cap drops 6000 -> 4000 (ample for a 6-archetype batch). Coverage
  of every name is enforced by the enumerated-name prompt + the runtime
  missing-name guard in ``draft_character_profiles``.
- AC-PROD-R13-FINAL-1 — `final_profile_writer` must use `gpt-4o-mini` (was
  `gemini/gemini-flash-latest`). The R7 "no OpenAI key in prod" rationale was
  superseded by AC-PROD-R11-INFRA-1, which wired OPENAI_API_KEY into Key Vault
  and the deploy pipeline. The eval showed flash-latest at ~1% validity here
  (empty/unparseable output) while gpt-4o-mini was 100% valid + 100%
  substantive.
"""
from __future__ import annotations

from pathlib import Path

import yaml  # type: ignore[import-untyped]


def _load_llm_tools() -> dict:
    cfg_path = (
        Path(__file__).resolve().parents[3] / "appconfig.local.yaml"
    )
    with cfg_path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    return data["quizzical"]["llm"]["tools"]


def test_profile_batch_writer_uses_gpt4o_mini() -> None:
    """AC-EVAL-2026-07-01: profile_batch_writer SWITCHES to gpt-4o-mini.

    The prior revision kept gemini-flash-latest because gpt-4o-mini dropped names
    (0/50 coverage on the 2026-06-29 run). A fresh targeted live eval (reps=20,
    judge gemini-2.5-pro) with the NEW quality-rubric prompt found gpt-4o-mini now
    STRICTLY DOMINATES flash-latest: quality 2.81 vs 2.69 (Δ+0.13, paired-t
    p=0.004), ~15x cheaper ($0.67 vs $9.95/1k), 100% valid (vs 90%), and coverage
    up to 91% — the rubric prompt's completeness emphasis fixed the name-dropping;
    the per-character profile_writer fallback backfills the rest. See
    evals/REPORT-PBW-2026-07-01.md.
    """
    pbw = _load_llm_tools()["profile_batch_writer"]
    assert pbw["model"] == "gpt-4o-mini", (
        "profile_batch_writer must use gpt-4o-mini per the 2026-07-01 eval "
        "(15x cheaper + higher quality + 100% valid; coverage 91% with fallback). "
        f"Got {pbw['model']!r}."
    )
    # A 6-archetype batch of 2-3 paragraph profiles needs ample output budget.
    assert pbw["max_output_tokens"] >= 2000, (
        "profile_batch_writer needs enough output budget for the full roster."
    )


def test_final_profile_writer_uses_gpt4o_mini() -> None:
    """AC-PROD-R13-FINAL-1."""
    fpw = _load_llm_tools()["final_profile_writer"]
    assert fpw["model"] == "gpt-4o-mini", (
        "final_profile_writer must use gpt-4o-mini per R13 — flash-latest was "
        "~1% valid in the eval (empty/unparseable output) while gpt-4o-mini "
        "was 100% valid + substantive. The R7 'no OpenAI key' blocker was "
        f"removed by AC-PROD-R11-INFRA-1. Got {fpw['model']!r}."
    )
    # The reading is 3-5 paragraphs; a non-reasoning model needs ~1.5k visible
    # tokens. Keep a sane upper bound.
    assert fpw["max_output_tokens"] <= 2000
