"""Hitlist #9 — fail-closed startup assertion for LLM provider keys.

The live agent loop hard-depends on the providers referenced by
``quizzical.llm.tools.*.model``. In prod-class envs the matching key secret
must be present and non-placeholder, or the app must refuse to start.
"""

from __future__ import annotations

import pytest

from app.services.precompute.secrets import (
    _provider_for_model,
    assert_llm_provider_keys_or_fail_closed,
)

# A representative slice of the real appconfig tool models.
TOOL_MODELS = {
    "initial_planner": "gpt-4o-mini",
    "question_generator": "gpt-4o-mini",
    "decision_maker": "gpt-4o-mini",
    "synopsis_generator": "gemini/gemini-2.5-flash",
    "profile_batch_writer": "gemini/gemini-flash-latest",
}


def _lookup(mapping: dict[str, str]):
    return lambda name: mapping.get(name)


@pytest.mark.parametrize(
    "model,provider",
    [
        ("gpt-4o-mini", "openai"),
        ("openai/gpt-4o", "openai"),
        ("o3-mini", "openai"),
        ("gemini/gemini-2.5-flash", "gemini"),
        ("gemini/gemini-flash-latest", "gemini"),
        ("groq/llama-3.1", "groq"),
        ("anthropic/claude-3-5", "anthropic"),
        ("claude-3-opus", "anthropic"),
        ("BAAI/bge-small-en-v1.5", None),  # local embedder, not a chat provider
        ("", None),
        (None, None),
    ],
)
def test_provider_for_model(model, provider):
    assert _provider_for_model(model) == provider


@pytest.mark.parametrize("env", ["local", "dev", "test", "staging", "ci"])
def test_non_prod_never_raises_even_with_no_keys(env):
    # All keys unset -> must NOT raise in non-prod.
    assert_llm_provider_keys_or_fail_closed(
        environment=env,
        tool_models=TOOL_MODELS,
        env_lookup=_lookup({}),
    )


def test_prod_passes_when_all_referenced_keys_present():
    assert_llm_provider_keys_or_fail_closed(
        environment="production",
        tool_models=TOOL_MODELS,
        env_lookup=_lookup(
            {"OPENAI_API_KEY": "sk-real-openai", "GEMINI_API_KEY": "g-real-gemini"}
        ),
    )


def test_prod_fails_closed_when_openai_key_missing():
    with pytest.raises(RuntimeError) as exc:
        assert_llm_provider_keys_or_fail_closed(
            environment="production",
            tool_models=TOOL_MODELS,
            env_lookup=_lookup({"GEMINI_API_KEY": "g-real-gemini"}),
        )
    msg = str(exc.value)
    assert "OPENAI_API_KEY" in msg
    assert "GEMINI_API_KEY" not in msg  # gemini key was present


def test_prod_fails_closed_when_gemini_key_missing():
    with pytest.raises(RuntimeError) as exc:
        assert_llm_provider_keys_or_fail_closed(
            environment="production",
            tool_models=TOOL_MODELS,
            env_lookup=_lookup({"OPENAI_API_KEY": "sk-real-openai"}),
        )
    assert "GEMINI_API_KEY" in str(exc.value)


def test_prod_fails_closed_on_placeholder_key():
    with pytest.raises(RuntimeError):
        assert_llm_provider_keys_or_fail_closed(
            environment="production",
            tool_models={"initial_planner": "gpt-4o-mini"},
            env_lookup=_lookup({"OPENAI_API_KEY": "your_openai_api_key"}),
        )


def test_unknown_env_treated_as_prod_and_fails_closed():
    # An unrecognised env (e.g. typo'd 'azure') must fail closed, not skip.
    with pytest.raises(RuntimeError):
        assert_llm_provider_keys_or_fail_closed(
            environment="azure",
            tool_models={"initial_planner": "gpt-4o-mini"},
            env_lookup=_lookup({}),
        )


def test_no_referenced_providers_never_raises_in_prod():
    # Only a local/non-chat model referenced -> nothing to assert.
    assert_llm_provider_keys_or_fail_closed(
        environment="production",
        tool_models={"embedder": "BAAI/bge-small-en-v1.5"},
        env_lookup=_lookup({}),
    )
