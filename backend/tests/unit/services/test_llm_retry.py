# backend/tests/unit/services/test_llm_retry.py
"""§16.1 — AC-LLM-RETRY-1..5: LiteLLM transient-error retry.

These tests exercise the retry behaviour wired into
``LLMService.get_structured_response`` via ``app.services.retry.retry_async``.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, List
from unittest.mock import patch

import litellm
import pytest
from pydantic import BaseModel

from app.core.config import settings
from app.services import llm_service as llm_mod
from app.services import retry as retry_mod


class _Model(BaseModel):
    name: str
    age: int


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Make retry sleeps instant so tests stay fast."""
    async def _sleep(_s: float) -> None:
        return None

    monkeypatch.setattr(retry_mod.asyncio, "sleep", _sleep)


@pytest.fixture
def collector(monkeypatch):
    """Patch litellm.responses + asyncio.to_thread so we control raise/return.

    ``calls`` records arg payloads; ``raises`` is a list of exceptions to
    raise on each successive call (then fall through to ``ok_resp``).
    """
    state: dict[str, Any] = {"calls": 0, "raises": [], "ok_resp": None}

    def _sync_responses(**kwargs):
        state["calls"] += 1
        if state["raises"]:
            exc = state["raises"].pop(0)
            if exc is not None:
                raise exc
        return state["ok_resp"]

    async def _fake_to_thread(func, **kwargs):
        return func(**kwargs)

    monkeypatch.setattr(llm_mod.asyncio, "to_thread", _fake_to_thread)
    monkeypatch.setattr(llm_mod.litellm, "responses", _sync_responses)
    return state


# ---------------------------------------------------------------------------
# AC-LLM-RETRY-1: transient errors are retried up to max_attempts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc_factory",
    [
        lambda: asyncio.TimeoutError("simulated timeout"),
        # LiteLLM exceptions accept varied signatures across versions; use
        # message=... which their current __init__ tolerates universally.
        lambda: litellm.Timeout(message="t", model="m", llm_provider="x"),
        lambda: litellm.APIConnectionError(message="c", model="m", llm_provider="x"),
        lambda: litellm.RateLimitError(message="r", model="m", llm_provider="x"),
        lambda: litellm.InternalServerError(message="i", model="m", llm_provider="x"),
        lambda: litellm.ServiceUnavailableError(message="s", model="m", llm_provider="x"),
    ],
)
async def test_llm_retry_recovers_after_transient_error(monkeypatch, collector, exc_factory):
    # max_attempts=3 → after 2 failures the 3rd call succeeds
    monkeypatch.setattr(settings.llm.retry, "max_attempts", 3)
    monkeypatch.setattr(settings.llm.retry, "base_ms", 1)
    monkeypatch.setattr(settings.llm.retry, "cap_ms", 2)
    collector["raises"] = [exc_factory(), exc_factory()]
    collector["ok_resp"] = SimpleNamespace(output_parsed={"name": "OK", "age": 1})

    svc = llm_mod.LLMService()
    res = await svc.get_structured_response(
        tool_name="t", messages=[], response_model=_Model
    )

    assert isinstance(res, _Model)
    assert collector["calls"] == 3


# ---------------------------------------------------------------------------
# AC-LLM-RETRY-2: non-transient errors are NOT retried
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc_factory",
    [
        lambda: litellm.AuthenticationError(message="a", model="m", llm_provider="x"),
        lambda: litellm.BadRequestError(message="b", model="m", llm_provider="x"),
        lambda: ValueError("programmer bug"),
    ],
)
async def test_llm_retry_skips_non_transient(monkeypatch, collector, exc_factory):
    monkeypatch.setattr(settings.llm.retry, "max_attempts", 5)
    monkeypatch.setattr(settings.llm.retry, "base_ms", 1)
    monkeypatch.setattr(settings.llm.retry, "cap_ms", 2)
    # Even with 5 attempts available, a non-transient error must not retry.
    collector["raises"] = [exc_factory(), exc_factory(), exc_factory()]

    svc = llm_mod.LLMService()
    with pytest.raises(Exception):
        await svc.get_structured_response(
            tool_name="t", messages=[], response_model=_Model
        )

    assert collector["calls"] == 1


# ---------------------------------------------------------------------------
# AC-LLM-RETRY-4: max_attempts=1 disables retry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_llm_retry_disabled_when_max_attempts_one(monkeypatch, collector):
    monkeypatch.setattr(settings.llm.retry, "max_attempts", 1)
    collector["raises"] = [
        asyncio.TimeoutError("t1"),
        asyncio.TimeoutError("t2"),
    ]

    svc = llm_mod.LLMService()
    with pytest.raises(asyncio.TimeoutError):
        await svc.get_structured_response(
            tool_name="t", messages=[], response_model=_Model
        )

    assert collector["calls"] == 1


# ---------------------------------------------------------------------------
# AC-LLM-RETRY-1: exhausted retries re-raise the last transient error
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_llm_retry_exhaustion_raises_last_exception(monkeypatch, collector):
    monkeypatch.setattr(settings.llm.retry, "max_attempts", 3)
    monkeypatch.setattr(settings.llm.retry, "base_ms", 1)
    monkeypatch.setattr(settings.llm.retry, "cap_ms", 2)
    last = asyncio.TimeoutError("final")
    collector["raises"] = [
        asyncio.TimeoutError("first"),
        asyncio.TimeoutError("second"),
        last,
    ]

    svc = llm_mod.LLMService()
    with pytest.raises(asyncio.TimeoutError) as ei:
        await svc.get_structured_response(
            tool_name="t", messages=[], response_model=_Model
        )

    assert collector["calls"] == 3
    assert str(ei.value) == "final"


# ---------------------------------------------------------------------------
# AC-LLM-RETRY-3: retry attempts emit a structured warning log
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_llm_retry_emits_warning_log(monkeypatch, collector):
    monkeypatch.setattr(settings.llm.retry, "max_attempts", 3)
    monkeypatch.setattr(settings.llm.retry, "base_ms", 1)
    monkeypatch.setattr(settings.llm.retry, "cap_ms", 2)
    collector["raises"] = [asyncio.TimeoutError("once")]
    collector["ok_resp"] = SimpleNamespace(output_parsed={"name": "OK", "age": 1})

    # Capture structlog events directly via patching the bound logger's warning.
    seen: List[dict] = []
    real_warning = llm_mod.logger.warning

    def _capturing_warning(event: str, **kw):
        seen.append({"event": event, **kw})
        return real_warning(event, **kw)

    monkeypatch.setattr(llm_mod.logger, "warning", _capturing_warning)

    svc = llm_mod.LLMService()
    await svc.get_structured_response(
        tool_name="t", messages=[], response_model=_Model
    )

    assert any(e["event"] == "llm.structured.retrying" for e in seen)
    retry_evt = next(e for e in seen if e["event"] == "llm.structured.retrying")
    assert retry_evt["attempt"] == 1
    assert retry_evt["tool"] == "t"
