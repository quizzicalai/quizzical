# backend/tests/unit/agent/test_appconfig_models_round11.py
"""Round 11 regression tests for production model selection (perf swap).

Spec refs:
- AC-PROD-R11-PERF-1 — `next_question_generator` must use `gpt-4o-mini`
  (3× faster than `gemini-flash-latest` per Analysis/perf_per_tool.py:
  4.4 s vs 14.2 s mean, 7.6 s vs 26.9 s p95, 5/5 validity each).
- AC-PROD-R11-PERF-2 — `decision_maker` must use `gpt-4o-mini`
  (1.5 s vs 3.4 s mean).
- AC-PROD-R11-PERF-3 — non-reasoning models do not need an inflated
  `max_output_tokens` budget; the swap drops NQG cap 6000 → 1500 and
  decision_maker 4000 → 500.
- AC-PROD-R11-INFRA-2 — runtime fallback in `llm_service` substitutes
  the model when the provider key is missing, so the deploy is safe
  even before `OPENAI_API_KEY` is wired in Key Vault.
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


def test_next_question_generator_uses_gpt4o_mini() -> None:
    """AC-PROD-R11-PERF-1."""
    tools = _load_llm_tools()
    nqg = tools["next_question_generator"]
    assert nqg["model"] == "gpt-4o-mini", (
        "next_question_generator must use gpt-4o-mini per the R11 perf "
        "swap. Per-tool benchmark (5 reps, prod-shape prompts):\n"
        "  gpt-4o-mini:               mean 4.4s p95 7.6s validity 5/5\n"
        "  gemini-flash-latest (was): mean 14.2s p95 26.9s validity 5/5\n"
        f"Got {nqg['model']!r}."
    )


def test_decision_maker_uses_gpt4o_mini() -> None:
    """AC-PROD-R11-PERF-2."""
    tools = _load_llm_tools()
    dm = tools["decision_maker"]
    assert dm["model"] == "gpt-4o-mini", (
        "decision_maker must use gpt-4o-mini per the R11 perf swap. "
        "Per-tool benchmark (5 reps): gpt-4o-mini mean 1.5s vs "
        f"gemini-flash-latest 3.4s. Got {dm['model']!r}."
    )


def test_hotspot_tools_drop_reasoning_token_budget() -> None:
    """AC-PROD-R11-PERF-3.

    gpt-4o-mini emits no reasoning tokens, so the inflated R10 budgets
    (NQG=6000, DM=4000) are pure waste and slow the response. The R11
    config tightens both caps. This test guards against accidentally
    leaving the reasoning-era budgets in place if someone reverts the
    model swap without updating the budgets.
    """
    tools = _load_llm_tools()
    nqg = tools["next_question_generator"]
    dm = tools["decision_maker"]
    assert nqg["max_output_tokens"] <= 2000, (
        "next_question_generator max_output_tokens should be ≤ 2000 for "
        "non-reasoning models; the R10 6000 cap exists only to budget for "
        "Gemini reasoning tokens."
    )
    assert dm["max_output_tokens"] <= 1000, (
        "decision_maker max_output_tokens should be ≤ 1000 for "
        "non-reasoning models; the decision JSON is ~90 chars."
    )


def test_substitute_model_if_key_missing_falls_back_when_openai_unset(
    monkeypatch,
) -> None:
    """AC-PROD-R11-INFRA-2 — runtime safety net.

    When `OPENAI_API_KEY` is missing, gpt-4o-mini calls must transparently
    fall back to a Gemini model so the deploy stays green even before the
    GitHub Actions secret is wired through Key Vault.
    """
    from app.services.llm_service import _substitute_model_if_key_missing

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    out = _substitute_model_if_key_missing("gpt-4o-mini", tool_name="nqg")
    assert out.startswith("gemini/"), (
        f"Expected Gemini fallback when OPENAI_API_KEY is missing; got {out!r}"
    )


def test_substitute_model_if_key_missing_passthrough_when_key_present(
    monkeypatch,
) -> None:
    """AC-PROD-R11-INFRA-2 — happy path."""
    from app.services.llm_service import _substitute_model_if_key_missing

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-real-value")
    out = _substitute_model_if_key_missing("gpt-4o-mini", tool_name="nqg")
    assert out == "gpt-4o-mini", (
        f"Expected pass-through when OPENAI_API_KEY is set; got {out!r}"
    )


def test_substitute_model_if_key_missing_passthrough_for_gemini(
    monkeypatch,
) -> None:
    """Gemini models are unaffected by the OpenAI key check."""
    from app.services.llm_service import _substitute_model_if_key_missing

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    out = _substitute_model_if_key_missing(
        "gemini/gemini-flash-latest", tool_name="planner"
    )
    assert out == "gemini/gemini-flash-latest"
