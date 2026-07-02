"""Prompt-fidelity tests for the eval harness (AC-EVAL-2026-07-02, punchlist P7).

``baseline`` must render the PRODUCTION-effective prompt — the App-Config
override from ``backend/appconfig.local.yaml`` ``llm.prompts`` when present,
else the code default — exactly like ``PromptManager.get_prompt``. Before this
fix the harness always scored ``DEFAULT_PROMPTS``, i.e. text production never
sends for the overridden functions.

These tests are skipped when the backend isn't importable (dry-run isolation);
in that mode the adapter's stub fallback is exercised instead.
"""
from __future__ import annotations

import pytest

from quizzical_evals.prompts_adapter import _ensure_backend_on_path, get_prompt_pair

backend_available = _ensure_backend_on_path()
needs_backend = pytest.mark.skipif(
    not backend_available, reason="backend not importable in this environment"
)


@needs_backend
@pytest.mark.parametrize(
    "function", ["initial_planner", "question_generator", "next_question_generator"]
)
def test_baseline_prefers_appconfig_override(function: str) -> None:
    from app.core.config import settings

    cfg = settings.llm_prompts.get(function)
    if not (cfg and cfg.system_prompt and cfg.user_prompt_template):
        pytest.skip(f"no App-Config override for {function} in this checkout")
    system, user = get_prompt_pair(function, "baseline")
    assert system == cfg.system_prompt
    assert user == cfg.user_prompt_template


@needs_backend
def test_baseline_falls_back_to_default_when_no_override() -> None:
    from app.agent.prompts import DEFAULT_PROMPTS

    # profile_batch_writer has no llm.prompts override -> code default.
    system, user = get_prompt_pair("profile_batch_writer", "baseline")
    assert (system, user) == DEFAULT_PROMPTS["profile_batch_writer"]


@needs_backend
def test_default_strategy_ignores_override() -> None:
    from app.agent.prompts import DEFAULT_PROMPTS

    system, user = get_prompt_pair("question_generator", "default")
    assert (system, user) == DEFAULT_PROMPTS["question_generator"]


@needs_backend
def test_cot_transform_builds_on_code_default() -> None:
    from app.agent.prompts import DEFAULT_PROMPTS

    d_system, d_user = DEFAULT_PROMPTS["question_generator"]
    system, user = get_prompt_pair("question_generator", "cot")
    assert system.startswith(d_system) and system != d_system
    assert user.endswith(d_user) and user != d_user


@needs_backend
def test_overridden_baseline_keeps_forbidden_block() -> None:
    """The shipped QG/NQG overrides must carry the anti-self-match block."""
    for fn in ("question_generator", "next_question_generator"):
        _, user = get_prompt_pair(fn, "baseline")
        assert "ABSOLUTELY FORBIDDEN" in user, fn
