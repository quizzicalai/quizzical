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


def test_profile_batch_writer_stays_flash_for_coverage() -> None:
    """AC-PROD-R13-PERF-1 (revised): profile_batch_writer KEEPS gemini-flash-latest.

    gpt-4o-mini is ~13x cheaper + higher judged quality, but a confirmatory live
    run (2026-06-29, reps=10, with the new enumerated-names prompt) found it
    covered ALL character names in only 0/50 batches vs flash-latest 50/50 — it
    drops characters, which would break the result. The prompt hardening +
    missing-name guard did NOT close the gap, so the swap is held until a real
    coverage fix (per-character calls / batch split).
    """
    pbw = _load_llm_tools()["profile_batch_writer"]
    assert pbw["model"] == "gemini/gemini-flash-latest", (
        "profile_batch_writer must stay on gemini-flash-latest — gpt-4o-mini "
        "failed character-coverage (0/50 in the confirmatory live eval). "
        f"Got {pbw['model']!r}."
    )
    # Flash is a reasoning model; keep generous output headroom for a full
    # multi-archetype batch (it used ~3.3k visible+reasoning tokens in the eval).
    assert pbw["max_output_tokens"] >= 4000, (
        "profile_batch_writer needs ample output budget for flash's reasoning "
        "tax plus the full roster."
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
