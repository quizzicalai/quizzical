# backend/tests/unit/agent/test_appconfig_models_round12.py
"""Round 12 regression tests for production model selection (perf swap).

Spec refs:
- AC-PROD-R12-PERF-1 — `profile_writer` must use `gpt-4o-mini` (was
  `gemini/gemini-2.5-flash`). Eliminates hidden-CoT empty-output
  failures that caused per-character retry storms.
- AC-PROD-R12-PERF-2 — `question_generator` must use `gpt-4o-mini`
  (was `gemini/gemini-flash-latest`). Critical-path baseline batch.
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


def test_profile_writer_uses_gpt4o_mini() -> None:
    """AC-PROD-R12-PERF-1."""
    pw = _load_llm_tools()["profile_writer"]
    assert pw["model"] == "gpt-4o-mini", (
        "profile_writer must use gpt-4o-mini per R12 (Gemini reasoning "
        "models intermittently emit zero text_tokens, causing parser "
        f"retries and user timeouts). Got {pw['model']!r}."
    )
    assert pw["max_output_tokens"] <= 1000, (
        "profile_writer max_output_tokens should be ≤ 1000 — gpt-4o-mini "
        "has no reasoning-token tax so the R10 1600 budget is wasteful."
    )


def test_question_generator_uses_gpt4o_mini() -> None:
    """AC-PROD-R12-PERF-2."""
    qg = _load_llm_tools()["question_generator"]
    assert qg["model"] == "gpt-4o-mini", (
        "question_generator must use gpt-4o-mini per R12. Critical path: "
        "user sees nothing until this baseline batch returns. "
        f"Got {qg['model']!r}."
    )
    assert qg["max_output_tokens"] <= 3000, (
        "question_generator max_output_tokens should be ≤ 3000 — non-"
        "reasoning model needs no hidden-CoT budget."
    )
