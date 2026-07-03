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


# Owner decision 2026-07-02: "staging"/"ci" are PRODUCTION-classified now
# (fail-closed) — see NON_PROD_ENVS in app.core.config.
@pytest.mark.parametrize("env", ["local", "dev", "test"])
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


def test_prod_allows_missing_openai_when_gemini_present(caplog):
    # FALLBACK-AWARE (#9 fix A): a missing OPENAI_API_KEY is NOT a hard failure
    # when GEMINI_API_KEY is present — llm_service substitutes gemini for gpt-*
    # models and api-deploy.yml deliberately skips an empty OPENAI secret. The
    # gate must allow boot (and only WARN). This mirrors the real prod deploy.
    assert_llm_provider_keys_or_fail_closed(
        environment="production",
        tool_models=TOOL_MODELS,
        env_lookup=_lookup({"GEMINI_API_KEY": "g-real-gemini"}),
    )


def test_prod_fails_closed_when_gemini_key_missing():
    # GEMINI_API_KEY is the universal fallback target AND profile_batch_writer
    # uses gemini directly with no fallback -> a missing gemini key fails closed
    # even when OpenAI is present.
    with pytest.raises(RuntimeError) as exc:
        assert_llm_provider_keys_or_fail_closed(
            environment="production",
            tool_models=TOOL_MODELS,
            env_lookup=_lookup({"OPENAI_API_KEY": "sk-real-openai"}),
        )
    assert "GEMINI_API_KEY" in str(exc.value)


def test_prod_fails_closed_when_both_openai_and_gemini_missing():
    # openai's only fallback is gemini; with neither key present there is no
    # available fallback, so an openai-only roster fails closed.
    with pytest.raises(RuntimeError) as exc:
        assert_llm_provider_keys_or_fail_closed(
            environment="production",
            tool_models={"initial_planner": "gpt-4o-mini"},
            env_lookup=_lookup({}),
        )
    # The missing key surfaced is the (gemini) fallback target's, since openai
    # falls back to it; either way boot is refused.
    assert "Refusing to start" in str(exc.value)


def test_prod_fails_closed_on_placeholder_gemini_key():
    # Placeholder is treated as unset; gemini placeholder + no real key -> fail.
    with pytest.raises(RuntimeError):
        assert_llm_provider_keys_or_fail_closed(
            environment="production",
            tool_models={"synopsis_generator": "gemini/gemini-2.5-flash"},
            env_lookup=_lookup({"GEMINI_API_KEY": "your_gemini_api_key"}),
        )


def test_prod_allows_openai_placeholder_when_gemini_present():
    # An OpenAI placeholder is "unset" for openai, but the gemini fallback key
    # is present -> allow boot (warn), consistent with graceful degradation.
    assert_llm_provider_keys_or_fail_closed(
        environment="production",
        tool_models={"initial_planner": "gpt-4o-mini"},
        env_lookup=_lookup(
            {"OPENAI_API_KEY": "your_openai_api_key", "GEMINI_API_KEY": "g-real"}
        ),
    )


def test_unknown_env_treated_as_prod_and_fails_closed():
    # An unrecognised env (e.g. typo'd 'azure') must fail closed, not skip,
    # when no provider key (and no fallback) is available.
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
