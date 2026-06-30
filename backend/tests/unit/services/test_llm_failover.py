# backend/tests/unit/services/test_llm_failover.py
"""Hitlist #4 (2026-06-30) — runtime CROSS-provider LLM failover.

The whole critical path runs on one provider (gpt-4o-mini / OpenAI). Before
this change the only fallback (``_substitute_model_if_key_missing``) fired ONLY
on key-ABSENCE at startup; a runtime 429/5xx/timeout exhausted the same-provider
retries then failed the whole agent run. These tests prove the new behaviour:

  * a TERMINAL provider error (transient class, after the in-provider retries)
    triggers EXACTLY ONE retry on a CROSS-provider fallback model;
  * the fallback result still validates against the same schema;
  * a DETERMINISTIC error (parse / validation / programming) does NOT fail over;
  * when the fallback ALSO fails, a terminal-exhaustion log is emitted and the
    error is surfaced — no loop, no double-spend beyond the single fallback;
  * ``fallback_model=""`` disables failover entirely.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, List

import pytest
from pydantic import BaseModel

from app.services import llm_service as llm_mod
from app.services import retry as retry_mod

pytestmark = pytest.mark.asyncio


class _Model(BaseModel):
    name: str
    age: int


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _sleep(_s: float) -> None:
        return None

    monkeypatch.setattr(retry_mod.asyncio, "sleep", _sleep)


@pytest.fixture(autouse=True)
def _single_attempt(monkeypatch):
    """Force max_attempts=1 so each provider gets EXACTLY ONE call — this makes
    the total-call-count assertions a direct proxy for 'how many provider
    attempts happened' (1 primary + at most 1 fallback)."""
    from app.core.config import settings

    monkeypatch.setattr(settings.llm.retry, "max_attempts", 1)


@pytest.fixture
def collector(monkeypatch):
    """Patch ``litellm.responses`` (run via to_thread) so we control per-model
    behaviour. ``by_model`` maps a model string to either an exception to raise
    or a response object to return; ``calls`` records (model) per invocation."""
    state: dict[str, Any] = {"calls": [], "by_model": {}, "default": None}

    def _sync_responses(**kwargs):
        model = kwargs.get("model")
        state["calls"].append(model)
        behaviour = state["by_model"].get(model, state["default"])
        if isinstance(behaviour, BaseException):
            raise behaviour
        return behaviour

    async def _fake_to_thread(func, **kwargs):
        return func(**kwargs)

    monkeypatch.setattr(llm_mod.asyncio, "to_thread", _fake_to_thread)
    monkeypatch.setattr(llm_mod.litellm, "responses", _sync_responses)
    return state


def _ok_resp() -> SimpleNamespace:
    return SimpleNamespace(output_parsed={"name": "OK", "age": 1})


def _transient() -> asyncio.TimeoutError:
    return asyncio.TimeoutError("provider down")


# ---------------------------------------------------------------------------
# Primary terminal-transient error -> EXACTLY ONE cross-provider fallback.
# ---------------------------------------------------------------------------
async def test_failover_on_terminal_transient_succeeds_on_fallback(collector):
    collector["by_model"] = {
        "gpt-4o-mini": _transient(),          # primary terminal error
        "gemini/gemini-flash-latest": _ok_resp(),  # cross-provider fallback ok
    }

    svc = llm_mod.LLMService()
    res = await svc.get_structured_response(
        tool_name="next_question_generator",
        messages=[],
        response_model=_Model,
        model="gpt-4o-mini",
    )

    assert isinstance(res, _Model)  # fallback result still validates
    # EXACTLY ONE primary attempt + EXACTLY ONE fallback attempt.
    assert collector["calls"] == ["gpt-4o-mini", "gemini/gemini-flash-latest"]


async def test_failover_uses_explicit_fallback_model(collector, monkeypatch):
    # Provide the key so the explicit cross-provider fallback isn't itself
    # substituted by the startup key-absence guard (_substitute_model_if_key_missing).
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    collector["by_model"] = {
        "gpt-4o-mini": _transient(),
        "anthropic/claude-3-5-haiku-latest": _ok_resp(),
    }

    svc = llm_mod.LLMService()
    res = await svc.get_structured_response(
        tool_name="t",
        messages=[],
        response_model=_Model,
        model="gpt-4o-mini",
        fallback_model="anthropic/claude-3-5-haiku-latest",
    )

    assert isinstance(res, _Model)
    assert collector["calls"] == [
        "gpt-4o-mini",
        "anthropic/claude-3-5-haiku-latest",
    ]


# ---------------------------------------------------------------------------
# Deterministic errors must NOT fail over (a different provider can't fix a
# schema bug, and it would waste a paid call).
# ---------------------------------------------------------------------------
async def test_no_failover_on_deterministic_parse_failure(collector):
    # Primary call SUCCEEDS at the provider but returns an unparseable body ->
    # StructuredOutputError (deterministic). Must NOT fail over.
    collector["by_model"] = {
        "gpt-4o-mini": SimpleNamespace(output=[], output_parsed=None),
    }

    svc = llm_mod.LLMService()
    with pytest.raises(llm_mod.StructuredOutputError):
        await svc.get_structured_response(
            tool_name="t", messages=[], response_model=_Model, model="gpt-4o-mini"
        )

    assert collector["calls"] == ["gpt-4o-mini"]  # no fallback attempt


async def test_no_failover_on_validation_failure(collector):
    # Provider returns well-formed JSON that FAILS the schema (wrong type) ->
    # ValidationError-wrapped StructuredOutputError, deterministic -> no failover.
    collector["by_model"] = {
        "gpt-4o-mini": SimpleNamespace(output_parsed={"name": "x", "age": "NaN"}),
    }

    svc = llm_mod.LLMService()
    with pytest.raises(llm_mod.StructuredOutputError):
        await svc.get_structured_response(
            tool_name="t", messages=[], response_model=_Model, model="gpt-4o-mini"
        )

    assert collector["calls"] == ["gpt-4o-mini"]


# ---------------------------------------------------------------------------
# Fallback ALSO fails -> terminal-exhaustion log + error surfaced; NO loop.
# ---------------------------------------------------------------------------
async def test_failover_also_fails_emits_exhaustion_and_does_not_loop(
    collector, monkeypatch
):
    collector["by_model"] = {
        "gpt-4o-mini": _transient(),
        "gemini/gemini-flash-latest": _transient(),
    }

    seen: List[dict] = []
    real_error = llm_mod.logger.error

    def _capturing_error(event: str, **kw):
        seen.append({"event": event, **kw})
        return real_error(event, **kw)

    monkeypatch.setattr(llm_mod.logger, "error", _capturing_error)

    svc = llm_mod.LLMService()
    with pytest.raises(asyncio.TimeoutError):
        await svc.get_structured_response(
            tool_name="t", messages=[], response_model=_Model, model="gpt-4o-mini"
        )

    # EXACTLY two provider attempts total (1 primary + 1 fallback). No loop.
    assert collector["calls"] == ["gpt-4o-mini", "gemini/gemini-flash-latest"]
    # The terminal-exhaustion event makes the incident observable.
    exhausted = [e for e in seen if e["event"] == "llm.structured.failover.exhausted"]
    assert len(exhausted) == 1
    assert exhausted[0]["primary_model"] == "gpt-4o-mini"
    assert exhausted[0]["fallback_model"] == "gemini/gemini-flash-latest"


# ---------------------------------------------------------------------------
# fallback_model="" disables failover; no same-provider "failover".
# ---------------------------------------------------------------------------
async def test_empty_fallback_model_disables_failover(collector):
    collector["by_model"] = {"gpt-4o-mini": _transient()}

    svc = llm_mod.LLMService()
    with pytest.raises(asyncio.TimeoutError):
        await svc.get_structured_response(
            tool_name="t",
            messages=[],
            response_model=_Model,
            model="gpt-4o-mini",
            fallback_model="",
        )

    assert collector["calls"] == ["gpt-4o-mini"]  # no fallback


async def test_no_failover_when_fallback_is_same_provider(collector):
    # An explicit same-provider fallback is rejected (it can't escape a provider
    # incident) -> behaves like no fallback.
    collector["by_model"] = {"gpt-4o-mini": _transient()}

    svc = llm_mod.LLMService()
    with pytest.raises(asyncio.TimeoutError):
        await svc.get_structured_response(
            tool_name="t",
            messages=[],
            response_model=_Model,
            model="gpt-4o-mini",
            fallback_model="openai/gpt-4o",  # same provider family
        )

    assert collector["calls"] == ["gpt-4o-mini"]
