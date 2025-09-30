import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from pydantic import BaseModel
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage

from app.services import llm_service as llm_mod
from app.core.config import settings


# -------------------------
# Local helpers (this file)
# -------------------------

class _DemoModel(BaseModel):
    a: int
    b: str

class _FakeFunction:
    def __init__(self, name: str, arguments: Any):
        self.name = name
        self.arguments = arguments

class _FakeToolCall:
    def __init__(self, id: str, name: str, arguments: Any):
        self.id = id
        self.function = _FakeFunction(name, arguments)

class _FakeMessage:
    def __init__(self, *, content=None, parsed=None, tool_calls=None, refusal=None):
        self.content = content
        self.parsed = parsed
        self.tool_calls = tool_calls or []
        self.refusal = refusal

class _FakeChoice:
    def __init__(self, msg):
        self.message = msg

class _FakeResponse:
    def __init__(self, *, message: _FakeMessage, refusal=None, usage=None):
        self.choices = [_FakeChoice(message)]
        self.refusal = refusal
        self.usage = usage


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
    assert cj(bad) == {"text": "not json at all"}

    # non-string non-(dict/list) → as-is
    class X: pass
    x = X()
    assert cj(x) is x


def test_lc_to_openai_messages_role_and_content():
    msgs = [
        SystemMessage(content="sys"),
        HumanMessage(content="hi"),
        AIMessage(content="hello"),
        HumanMessage(content={"type": "input_image", "image_url": "http://x"}),
    ]
    converted = llm_mod._lc_to_openai_messages(msgs)
    assert [m["role"] for m in converted] == ["system", "user", "assistant", "user"]
    assert converted[0]["content"] == "sys"
    assert converted[1]["content"] == "hi"
    assert converted[2]["content"] == "hello"
    assert isinstance(converted[3]["content"], dict)
    assert converted[3]["content"]["type"] == "input_image"


def test_prepare_request_uses_model_cfg_and_api_key_and_metadata(monkeypatch):
    svc = llm_mod.LLMService()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-1234")

    msgs = [HumanMessage(content="hey")]
    req = svc._prepare_request("question_generator", msgs, trace_id="t-1", session_id="s-1")

    assert req["model"] == settings.llm_tools["question_generator"].model
    assert req["temperature"] == settings.llm_tools["question_generator"].temperature
    assert req["max_tokens"] == settings.llm_tools["question_generator"].max_output_tokens
    assert req["timeout"] == settings.llm_tools["question_generator"].timeout_s

    assert req["metadata"]["tool_name"] == "question_generator"
    assert req["metadata"]["trace_id"] == "t-1"
    assert req["metadata"]["session_id"] == "s-1"
    assert req.get("api_key") == "sk-test-1234"

    # Messages converted shape
    assert isinstance(req["messages"], list) and req["messages"][0]["role"] == "user"


def test_tool_cfg_fallback_when_missing():
    svc = llm_mod.LLMService()
    expected = settings.llm_tools.get("question_generator") or next(iter(settings.llm_tools.values()))
    got = svc._tool_cfg("nonexistent_tool_name")
    assert got.model == expected.model
    assert got.temperature == expected.temperature


@pytest.mark.asyncio
async def test_get_structured_response_returns_parsed(monkeypatch):
    svc = llm_mod.LLMService()

    parsed_obj = _DemoModel(a=1, b="ok")
    fake_resp = _FakeResponse(message=_FakeMessage(content=None, parsed=parsed_obj))

    captured_req: Dict[str, Any] = {}

    async def _fake_invoke(litellm_kwargs):
        nonlocal captured_req
        captured_req = dict(litellm_kwargs)
        return fake_resp

    monkeypatch.setattr(svc, "_invoke", _fake_invoke, raising=False)

    out = await svc.get_structured_response(
        tool_name="initial_planner",
        messages=[HumanMessage(content="hi")],
        response_model=_DemoModel,
        trace_id="T",
        session_id="S",
    )

    # Should return the exact parsed object
    assert isinstance(out, _DemoModel) and out == parsed_obj

    # Ensure response_format was set for structured output
    assert captured_req.get("response_format") is _DemoModel
    assert captured_req["metadata"]["tool_name"] == "initial_planner"


@pytest.mark.asyncio
async def test_get_structured_response_manual_json_fallback(monkeypatch):
    svc = llm_mod.LLMService()

    content = """```json
    {"a": 7, "b": "seven"}
    ```"""
    fake_resp = _FakeResponse(message=_FakeMessage(content=content, parsed=None))

    async def _fake_invoke(kwargs):
        return fake_resp

    monkeypatch.setattr(svc, "_invoke", _fake_invoke, raising=False)

    out = await svc.get_structured_response(
        tool_name="synopsis_generator",
        messages=[HumanMessage(content="go")],
        response_model=_DemoModel,
    )
    assert out.a == 7 and out.b == "seven"


@pytest.mark.asyncio
async def test_get_structured_response_refusal_raises(monkeypatch):
    svc = llm_mod.LLMService()

    # Refusal on the message
    fake_resp = _FakeResponse(
        message=_FakeMessage(content="no", refusal=SimpleNamespace(message="blocked"))
    )

    async def _fake_invoke(kwargs):
        return fake_resp

    monkeypatch.setattr(svc, "_invoke", _fake_invoke, raising=False)

    with pytest.raises(llm_mod.StructuredOutputError):
        await svc.get_structured_response(
            tool_name="profile_writer",
            messages=[HumanMessage(content="x")],
            response_model=_DemoModel,
        )


@pytest.mark.asyncio
async def test_get_agent_response_parses_tool_calls(monkeypatch):
    svc = llm_mod.LLMService()

    # arguments intentionally as a JSON string to exercise coerce_json
    tc = _FakeToolCall(id="call_1", name="lookup", arguments='{"query":"hi"}')
    fake_resp = _FakeResponse(message=_FakeMessage(content="ok", tool_calls=[tc]))

    async def _fake_invoke(kwargs):
        # Ensure tools/tool_choice make it through
        assert isinstance(kwargs.get("tools"), list)
        assert kwargs.get("tool_choice") == "auto"
        return fake_resp

    monkeypatch.setattr(svc, "_invoke", _fake_invoke, raising=False)

    out_msg = await svc.get_agent_response(
        tool_name="decision_maker",
        messages=[HumanMessage(content="start")],
        tools=[{"type": "function", "function": {"name": "lookup", "parameters": {}}}],
    )
    assert isinstance(out_msg, AIMessage)
    assert out_msg.content == "ok"
    assert out_msg.tool_calls and out_msg.tool_calls[0]["name"] == "lookup"
    assert out_msg.tool_calls[0]["args"] == {"query": "hi"}


@pytest.mark.asyncio
async def test_get_text_response_plain(monkeypatch):
    svc = llm_mod.LLMService()
    fake_resp = _FakeResponse(message=_FakeMessage(content="hello world"))

    async def _fake_invoke(kwargs):
        return fake_resp

    monkeypatch.setattr(svc, "_invoke", _fake_invoke, raising=False)

    text = await svc.get_text_response("safety_checker", [HumanMessage(content="ping")])
    assert text == "hello world"


@pytest.mark.asyncio
async def test_get_embedding_returns_empty_when_unavailable(monkeypatch):
    svc = llm_mod.LLMService()

    # Force the embedding path to behave as "unavailable"
    monkeypatch.setattr(llm_mod, "_embed_model", None, raising=False)
    monkeypatch.setattr(llm_mod, "_embed_import_error", "not installed", raising=False)

    vecs = await svc.get_embedding(["a", "b"])
    assert vecs == []  # tolerant failure returns empty list


def test_provider_heuristics():
    p = llm_mod._provider
    assert p(None) == "openai"
    assert p("openai/gpt-4o-mini") == "openai"
    assert p("gpt-4o-mini") == "openai"
    assert p("claude-3-haiku") == "anthropic"
    assert p("unknown-model-name") == "openai"
