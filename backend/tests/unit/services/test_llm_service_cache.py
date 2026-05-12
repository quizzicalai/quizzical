"""§9.7.8 — AC-LLM-CACHE-4/5: per-call cache opt-in/opt-out plumbing through
the LiteLLM payload metadata.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from app.services import llm_service as llm_mod


class _M(BaseModel):
    ok: bool


@pytest.fixture
def service():
    return llm_mod.LLMService()


@pytest.fixture
def mock_litellm(monkeypatch):
    """Capture the kwargs passed to litellm.responses for assertion."""
    container = {"kwargs": None, "resp": SimpleNamespace(output_parsed={"ok": True})}

    def _sync(**kwargs):
        container["kwargs"] = kwargs
        return container["resp"]

    async def _fake_to_thread(func, **kwargs):
        return func(**kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _fake_to_thread)
    monkeypatch.setattr(llm_mod.litellm, "responses", _sync)
    return container


class TestCachePerCallPlumbing:
    """AC-LLM-CACHE-4 / AC-LLM-CACHE-5."""

    @pytest.mark.asyncio
    async def test_cache_unset_adds_no_cache_metadata(self, service, mock_litellm):
        await service.get_structured_response(
            tool_name="t", messages=[], response_model=_M
        )
        meta = mock_litellm["kwargs"]["metadata"]
        assert "caching" not in meta
        assert "no-cache" not in meta

    @pytest.mark.asyncio
    async def test_cache_true_marks_payload_caching(self, service, mock_litellm):
        await service.get_structured_response(
            tool_name="t", messages=[], response_model=_M, cache=True
        )
        meta = mock_litellm["kwargs"]["metadata"]
        assert meta.get("caching") is True
        assert "no-cache" not in meta

    @pytest.mark.asyncio
    async def test_cache_false_marks_payload_no_cache(self, service, mock_litellm):
        await service.get_structured_response(
            tool_name="t", messages=[], response_model=_M, cache=False
        )
        meta = mock_litellm["kwargs"]["metadata"]
        assert meta.get("no-cache") is True
        assert "caching" not in meta

    @pytest.mark.asyncio
    async def test_existing_metadata_preserved(self, service, mock_litellm):
        await service.get_structured_response(
            tool_name="t",
            messages=[],
            response_model=_M,
            cache=True,
            metadata={"custom": "x"},
            trace_id="trace-1",
            session_id="sess-1",
        )
        meta = mock_litellm["kwargs"]["metadata"]
        assert meta.get("caching") is True
        assert meta.get("custom") == "x"
        assert meta.get("tool") == "t"
        assert meta.get("trace_id") == "trace-1"
        assert meta.get("session_id") == "sess-1"


class TestBuildPayloadCacheUnit:
    """Direct unit test on _build_litellm_payload to lock the contract."""

    def test_cache_none_no_keys(self):
        s = llm_mod.LLMService()
        p = s._build_litellm_payload(
            "gpt-4o-mini", [], {"type": "json_schema", "json_schema": {}}, None, None,
            None, None, None, {}, "tool", None, None, cache=None,
        )
        assert "caching" not in p["metadata"]
        assert "no-cache" not in p["metadata"]

    def test_cache_true_caching_key(self):
        s = llm_mod.LLMService()
        p = s._build_litellm_payload(
            "gpt-4o-mini", [], {"type": "json_schema", "json_schema": {}}, None, None,
            None, None, None, {}, "tool", None, None, cache=True,
        )
        assert p["metadata"]["caching"] is True

    def test_cache_false_no_cache_key(self):
        s = llm_mod.LLMService()
        p = s._build_litellm_payload(
            "gpt-4o-mini", [], {"type": "json_schema", "json_schema": {}}, None, None,
            None, None, None, {}, "tool", None, None, cache=False,
        )
        assert p["metadata"]["no-cache"] is True
