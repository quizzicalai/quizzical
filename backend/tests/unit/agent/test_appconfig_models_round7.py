# backend/tests/unit/agent/test_appconfig_models_round7.py
"""Round 7 regression tests for production model selection.

Spec refs:
- AC-PROD-R7-FINAL-1 / AC-PROD-R7-FINAL-2 — `final_profile_writer` must not
  depend on OpenAI in environments without `OPENAI_API_KEY`.
- AC-PROD-R7-DM-LAT-1 — `decision_maker` must use the non-reasoning
  `gemini-flash-latest` variant (the reasoning `gemini-2.5-flash` variant
  added 17-19 s of hidden CoT latency per cycle in prod).
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


def test_final_profile_writer_does_not_use_openai() -> None:
    """AC-PROD-R7-FINAL-1 / AC-PROD-R7-FINAL-2."""
    tools = _load_llm_tools()
    model = tools["final_profile_writer"]["model"]
    assert isinstance(model, str)
    assert not model.startswith("gpt-"), (
        "final_profile_writer must not use an OpenAI model — prod has no "
        "OPENAI_API_KEY and every call fails with an empty bearer token."
    )
    assert model.startswith("gemini/"), (
        f"final_profile_writer must use a Gemini model; got {model!r}."
    )


def test_decision_maker_uses_flash_latest() -> None:
    """AC-PROD-R7-DM-LAT-1."""
    tools = _load_llm_tools()
    dm = tools["decision_maker"]
    assert dm["model"] == "gemini/gemini-flash-latest", (
        "decision_maker must use gemini-flash-latest (non-reasoning); the "
        "reasoning gemini-2.5-flash variant added 17-19 s of hidden CoT "
        "latency per cycle in production."
    )
    # Latency safety net stays at >= 25 s.
    assert int(dm.get("timeout_s", 0)) >= 25
