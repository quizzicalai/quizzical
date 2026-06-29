# backend/tests/unit/agent/test_appconfig_models_round7.py
"""Round 7 regression tests for production model selection.

Spec refs:
- AC-PROD-R7-FINAL-1 / AC-PROD-R7-FINAL-2 — `final_profile_writer` must not
  depend on OpenAI in environments without `OPENAI_API_KEY`. SUPERSEDED by
  AC-PROD-R13-FINAL-1: AC-PROD-R11-INFRA-1 re-wired OPENAI_API_KEY into Key
  Vault + the deploy pipeline, so the tool now runs on gpt-4o-mini (100%
  valid in the eval vs flash-latest's ~1% empty/unparseable output).
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


def test_final_profile_writer_uses_a_provider_with_a_wired_key() -> None:
    """AC-PROD-R7-FINAL-1/2 SUPERSEDED BY AC-PROD-R13-FINAL-1.

    The R7 invariant ("must not use OpenAI, prod has no key") was conditioned
    on the Container App lacking ``OPENAI_API_KEY``. AC-PROD-R11-INFRA-1
    re-wired that key into Key Vault and the deploy pipeline for the
    gpt-4o-mini hotspot tools, so the precondition no longer holds. The eval
    showed flash-latest at ~1% validity here (empty/unparseable output from
    hidden-reasoning-token starvation) while gpt-4o-mini was 100% valid +
    100% substantive, so R13 moves the tool to gpt-4o-mini. The invariant
    that survives both rounds: the tool must run on a provider whose key is
    actually wired in every environment (gpt-4o-mini, like NQG/DM, or a
    Gemini model).
    """
    tools = _load_llm_tools()
    model = tools["final_profile_writer"]["model"]
    assert isinstance(model, str) and model.strip()
    assert model == "gpt-4o-mini" or model.startswith("gemini/"), (
        "final_profile_writer must run on a provider with a wired key "
        f"(gpt-4o-mini or a Gemini model); got {model!r}."
    )


def test_decision_maker_is_not_reasoning_heavy_gemini() -> None:
    """AC-PROD-R7-DM-LAT-1 (superseded model choice by AC-PROD-R11-PERF-2).

    The original R7 fix pinned `decision_maker` to `gemini-flash-latest`
    because the reasoning-heavy `gemini-2.5-flash` variant added 17-19 s of
    hidden CoT latency per cycle in production. R11 went further and moved
    the tool to `gpt-4o-mini` (3.4 s → 1.5 s mean). The invariant that
    matters across both rounds is the negative one: the tool must NOT use
    `gemini-2.5-flash` (the original offender). The positive R11 assertion
    lives in :mod:`tests.unit.agent.test_appconfig_models_round11`.
    """
    tools = _load_llm_tools()
    dm = tools["decision_maker"]
    assert dm["model"] != "gemini/gemini-2.5-flash", (
        "decision_maker must not regress to the reasoning-heavy "
        "gemini-2.5-flash variant — it added 17-19 s of hidden CoT per "
        "cycle in production. See AC-PROD-R7-DM-LAT-1."
    )
    # Latency safety net — every candidate model must finish within a
    # reasonable wall budget.
    assert 5 <= int(dm.get("timeout_s", 0)) <= 60
