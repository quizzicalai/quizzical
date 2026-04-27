"""§9.7.6 — LLM raw response size cap (AC-LLM-SIZE-1..3).

Defends against a buggy or compromised provider returning an
unreasonably large payload that would exhaust memory or stall
structured parsing. The guard runs after `litellm.responses` returns
and before structured extraction.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from app.core.config import settings
from app.services import llm_service as llm_mod


pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _FakeResp:
    """Mimics litellm.responses() return shape — a SDK-style object with
    `__dict__` carrying the `output` payload.
    """

    def __init__(self, payload: dict):
        self.id = "test-resp-1"
        # Many SDK objects expose attrs via __dict__; copy the payload
        # so `getattr(resp, '__dict__', None)` works in the service.
        for k, v in payload.items():
            setattr(self, k, v)


def _payload_of_size(approx_bytes: int) -> dict:
    """Build a payload whose JSON serialization is ~approx_bytes."""
    chunk = "x" * approx_bytes
    return {"output": [{"type": "text", "content": chunk}]}


@pytest.fixture(autouse=True)
def _restore_max_bytes():
    original = settings.llm.max_response_bytes
    yield
    settings.llm.max_response_bytes = original


async def _call_service(monkeypatch, payload: dict, max_bytes: int):
    """Invoke `LLMService.get_structured_response` with a stubbed litellm."""
    settings.llm.max_response_bytes = max_bytes

    # Stub litellm.responses to return our fake payload synchronously.
    def _fake_responses(**kwargs: Any):
        return _FakeResp(payload)

    monkeypatch.setattr(llm_mod.litellm, "responses", _fake_responses)

    # Minimal Pydantic-shaped validator that accepts anything.
    from pydantic import BaseModel

    class _Anything(BaseModel):
        model_config = {"extra": "allow"}

    svc = llm_mod.LLMService()
    return await svc.get_structured_response(
        tool_name="test_tool",
        messages=[{"role": "user", "content": "hi"}],
        response_model=_Anything,
        response_format={"type": "json_schema", "json_schema": {"name": "x", "schema": {"type": "object"}}},
    )


async def test_response_under_cap_passes(monkeypatch):
    """AC-LLM-SIZE-1: a small response is accepted (and parsing failure is not the size guard)."""
    payload = _payload_of_size(100)
    # Parsing will likely fail because our stub doesn't have proper structured
    # output, but the size guard must NOT be the cause.
    with pytest.raises(Exception) as exc_info:
        await _call_service(monkeypatch, payload, max_bytes=262144)
    assert not isinstance(exc_info.value, llm_mod.LLMResponseTooLargeError)


async def test_response_over_cap_raises(monkeypatch):
    """AC-LLM-SIZE-2: a response above the cap raises LLMResponseTooLargeError."""
    payload = _payload_of_size(5_000)
    with pytest.raises(llm_mod.LLMResponseTooLargeError) as exc_info:
        await _call_service(monkeypatch, payload, max_bytes=1_000)
    assert exc_info.value.size_bytes > 1_000
    assert exc_info.value.max_bytes == 1_000


async def test_size_cap_validator_rejects_zero(monkeypatch):
    """AC-LLM-SIZE-3: max_response_bytes must be >= 1 (config validator)."""
    from pydantic import ValidationError as PydanticValidationError

    with pytest.raises(PydanticValidationError):
        llm_mod.settings.llm.__class__(max_response_bytes=0)
