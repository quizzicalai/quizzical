# backend/tests/unit/services/test_llm_service.py

import asyncio
import json
import pytest
from types import SimpleNamespace
from typing import Any, Dict, List

from pydantic import BaseModel, ValidationError
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

# Import the module under test
from app.services import llm_service as llm_mod
from app.core.config import settings


# -------------------------
# Local helpers (this file)
# -------------------------

class _DemoModel(BaseModel):
    a: int
    b: str

class _FakeMessage:
    def __init__(self, *, content=None, parsed=None, refusal=None):
        self.content = content
        self.parsed = parsed
        self.refusal = refusal

class _FakeResponse:
    def __init__(self, *, message: _FakeMessage, id: str = "resp_123"):
        # The new llm_service looks for these attributes directly on the response object
        # or in the 'output' list if structured like the Responses API
        self.id = id
        self.output = [message]
        # Top-level parsed is checked first
        self.output_parsed = message.parsed
        # Top-level text convenience
        self.output_text = message.content

# ===========
# Unit tests
# ===========

@pytest.mark.asyncio
async def test_coerce_json_variants():
    cj = llm_mod.coerce_json

    # dict/list → as-is
    assert cj({"x": 1}) == {"x": 1}
    assert cj([1, 2]) == [1, 2]

    # plain JSON string
    assert cj('{"a":1}') == {"a": 1}

    # fenced json
    fenced = "```json\n{\"a\": 2}\n```"
    assert cj(fenced) == {"a": 2}

    # fenced (no language)
    fenced2 = "```\n{\"a\":3}\n```"
    assert cj(fenced2) == {"a": 3}

    # invalid json string → wrapped
    bad = "not json at all"
    assert cj(bad) == "not json at all" # Updated behavior: returns original if not parseable

    # non-string non-(dict/list) → as-is
    class X: pass
    x = X()
    assert cj(x) is x


def test_messages_to_input_conversion():
    """Verify conversion of LangChain messages to Responses API input format."""
    msgs = [
        SystemMessage(content="sys"),
        HumanMessage(content="hi"),
        AIMessage(content="hello"),
        # Dict input
        {"role": "user", "content": "raw dict"}
    ]
    converted = llm_mod._messages_to_input(msgs)
    
    assert len(converted) == 4
    assert converted[0] == {"role": "system", "content": "sys"}
    assert converted[1] == {"role": "user", "content": "hi"}
    assert converted[2] == {"role": "assistant", "content": "hello"}
    assert converted[3] == {"role": "user", "content": "raw dict"}


def test_build_litellm_payload_structure(monkeypatch):
    """Verify payload construction logic."""
    svc = llm_mod.LLMService()
    
    # Setup inputs
    model = "gpt-4o-mini"
    messages = [HumanMessage(content="hi")]
    rf = {"type": "json_schema", "json_schema": {"name": "test", "schema": {}}}
    
    payload = svc._build_litellm_payload(
        model=model,
        messages=messages,
        rf=rf,
        max_output_tokens=100,
        timeout_s=10,
        truncation=None,
        text_params={"temperature": 0.5},
        reasoning=None,
        metadata={"custom": "meta"},
        tool_name="test_tool",
        trace_id="t1",
        session_id="s1"
    )
    
    assert payload["model"] == model
    assert payload["max_output_tokens"] == 100
    assert payload["timeout"] == 10
    assert payload["response_format"] == rf
    assert payload["tool_choice"] == "none"
    
    # Metadata merging
    assert payload["metadata"]["tool"] == "test_tool"
    assert payload["metadata"]["trace_id"] == "t1"
    assert payload["metadata"]["custom"] == "meta"
    
    # Text params merged
    assert payload["temperature"] == 0.5


def test_is_reasoning_model():
    assert llm_mod._is_reasoning_model("gpt-5-mini") is True
    assert llm_mod._is_reasoning_model("o3-mini") is True
    assert llm_mod._is_reasoning_model("gpt-4o") is False


@pytest.mark.asyncio
async def test_get_structured_response_happy_path(monkeypatch):
    svc = llm_mod.LLMService()
    
    # Mock the response from litellm
    parsed_obj = {"a": 1, "b": "ok"}
    fake_resp = _FakeResponse(message=_FakeMessage(content=None, parsed=parsed_obj))
    
    # Mock litellm.responses
    async def _fake_responses(**kwargs):
        return fake_resp
        
    monkeypatch.setattr(llm_mod.litellm, "responses", _fake_responses, raising=True)
    
    # Monkeypatch to_thread to just run the func immediately (since we mocked it async above)
    # Actually, asyncio.to_thread runs a sync func in a thread.
    # Let's mock asyncio.to_thread to just return the result of our fake sync func.
    
    captured_kwargs = {}
    def _sync_fake_responses(**kwargs):
        nonlocal captured_kwargs
        captured_kwargs = kwargs
        return fake_resp

    async def _fake_to_thread(func, **kwargs):
        return func(**kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _fake_to_thread, raising=True)
    monkeypatch.setattr(llm_mod.litellm, "responses", _sync_fake_responses, raising=True)

    out = await svc.get_structured_response(
        tool_name="test_tool",
        messages=[HumanMessage(content="hi")],
        response_model=_DemoModel,
        trace_id="t1"
    )
    
    assert isinstance(out, _DemoModel)
    assert out.a == 1
    assert out.b == "ok"
    
    # Verify kwargs passed to litellm
    assert captured_kwargs["model"] == svc.default_model
    assert "response_format" in captured_kwargs


@pytest.mark.asyncio
async def test_get_structured_response_json_fallback(monkeypatch):
    svc = llm_mod.LLMService()
    
    # Response has no parsed object, but has JSON in text
    content = '{"a": 99, "b": "ninety-nine"}'
    fake_resp = _FakeResponse(message=_FakeMessage(content=content, parsed=None))
    
    def _sync_fake_responses(**kwargs):
        return fake_resp

    async def _fake_to_thread(func, **kwargs):
        return func(**kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _fake_to_thread, raising=True)
    monkeypatch.setattr(llm_mod.litellm, "responses", _sync_fake_responses, raising=True)

    out = await svc.get_structured_response(
        tool_name="test_tool",
        messages=[HumanMessage(content="hi")],
        response_model=_DemoModel,
    )
    
    assert out.a == 99


@pytest.mark.asyncio
async def test_get_structured_response_validation_error(monkeypatch):
    svc = llm_mod.LLMService()
    
    # Invalid shape for _DemoModel
    parsed_obj = {"a": "not-an-int", "b": "ok"}
    fake_resp = _FakeResponse(message=_FakeMessage(parsed=parsed_obj))
    
    def _sync_fake_responses(**kwargs):
        return fake_resp
    
    async def _fake_to_thread(func, **kwargs):
        return func(**kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _fake_to_thread, raising=True)
    monkeypatch.setattr(llm_mod.litellm, "responses", _sync_fake_responses, raising=True)

    with pytest.raises(llm_mod.StructuredOutputError) as exc:
        await svc.get_structured_response(
            tool_name="test_tool",
            messages=[HumanMessage(content="hi")],
            response_model=_DemoModel,
        )
    assert "validation failed" in str(exc.value)


@pytest.mark.asyncio
async def test_get_structured_response_parsing_failure(monkeypatch):
    svc = llm_mod.LLMService()
    
    # No parsed, no valid JSON text
    fake_resp = _FakeResponse(message=_FakeMessage(content="Just text", parsed=None))
    
    def _sync_fake_responses(**kwargs):
        return fake_resp
    
    async def _fake_to_thread(func, **kwargs):
        return func(**kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _fake_to_thread, raising=True)
    monkeypatch.setattr(llm_mod.litellm, "responses", _sync_fake_responses, raising=True)

    with pytest.raises(llm_mod.StructuredOutputError) as exc:
        await svc.get_structured_response(
            tool_name="test_tool",
            messages=[HumanMessage(content="hi")],
            response_model=_DemoModel,
        )
    assert "Could not locate/parse JSON" in str(exc.value)


@pytest.mark.asyncio
async def test_get_structured_response_api_error(monkeypatch):
    svc = llm_mod.LLMService()
    
    def _sync_boom(**kwargs):
        raise RuntimeError("API Down")
    
    async def _fake_to_thread(func, **kwargs):
        return func(**kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _fake_to_thread, raising=True)
    monkeypatch.setattr(llm_mod.litellm, "responses", _sync_boom, raising=True)

    with pytest.raises(RuntimeError, match="API Down"):
        await svc.get_structured_response(
            tool_name="test_tool",
            messages=[HumanMessage(content="hi")],
            response_model=_DemoModel,
        )


# ---------------------------------------------------------------------
# Text extraction helpers tests
# ---------------------------------------------------------------------

def test_strip_code_fences():
    assert llm_mod._strip_code_fences('```json\n{"a":1}\n```') == '{"a":1}'
    assert llm_mod._strip_code_fences('```\n[1, 2]\n```') == '[1, 2]'
    assert llm_mod._strip_code_fences('{"a":1}') == '{"a":1}'


def test_find_first_balanced_json():
    s = "Here is json: {\"a\": [1, 2]} end."
    assert llm_mod._find_first_balanced_json(s) == '{"a": [1, 2]}'
    
    s2 = "Invalid { start"
    assert llm_mod._find_first_balanced_json(s2) is None


def test_collect_text_parts():
    # Mock a complex response structure
    class MockPart:
        def __init__(self, text, type="text"):
            self.text = text
            self.type = type
            
    class MockItem:
        def __init__(self, parts):
            self.content = parts
            
    class MockResp:
        def __init__(self):
            self.output = [MockItem([MockPart("part1"), MockPart("part2")])]
            self.output_text = "top_level"
            
    resp = MockResp()
    parts = llm_mod._collect_text_parts(resp)
    assert "part1" in parts
    assert "part2" in parts
    assert "top_level" in parts


def test_extract_structured_priority():
    """Verify priority: top-level parsed > item parsed > text fallback."""
    # 1. Top level
    resp1 = SimpleNamespace(output_parsed={"k": "top"})
    assert llm_mod._extract_structured(resp1) == {"k": "top"}
    
    # 2. Item level
    resp2 = SimpleNamespace(output=[{"parsed": {"k": "item"}}])
    assert llm_mod._extract_structured(resp2) == {"k": "item"}
    
    # 3. Text fallback
    resp3 = SimpleNamespace(output_text='{"k": "text"}', output=[])
    assert llm_mod._extract_structured(resp3) == {"k": "text"}