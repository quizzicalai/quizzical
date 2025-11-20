import asyncio
import dataclasses
import json
import re
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

# Module under test
from app.services import llm_service as llm_mod
from app.core.config import settings

# ---------------------------------------------------------------------
# Test Data / Models
# ---------------------------------------------------------------------

class SimpleModel(BaseModel):
    name: str
    age: int

class NestedModel(BaseModel):
    items: List[SimpleModel]

@dataclasses.dataclass
class SimpleDataclass:
    x: int
    y: int

# ---------------------------------------------------------------------
# Utilities Tests
# ---------------------------------------------------------------------

def test_coerce_json():
    # 1. Pydantic
    p = SimpleModel(name="test", age=10)
    assert llm_mod.coerce_json(p) == {"name": "test", "age": 10}

    # 2. Dataclass
    d = SimpleDataclass(x=1, y=2)
    assert llm_mod.coerce_json(d) == {"x": 1, "y": 2}

    # 3. Generic Object with __dict__
    class GenericObj:
        def __init__(self):
            self.a = 1
    assert llm_mod.coerce_json(GenericObj()) == {"a": 1}

    # 4. Primitives / None
    assert llm_mod.coerce_json({"a": 1}) == {"a": 1}
    assert llm_mod.coerce_json([1, 2]) == [1, 2]
    assert llm_mod.coerce_json(None) is None

    # 5. Unserializable fallback
    # We use __slots__ to create an object without a __dict__, forcing the str() fallback
    class Broken:
        __slots__ = [] 
        def __str__(self):
            return "broken_str"

    assert llm_mod.coerce_json(Broken()) == "broken_str"

def test_asdict_shallow():
    # Dict pass-through
    assert llm_mod._asdict_shallow({"a": 1}) == {"a": 1}
    
    # Pydantic
    p = SimpleModel(name="p", age=1)
    assert llm_mod._asdict_shallow(p) == {"name": "p", "age": 1}
    
    # Dataclass
    d = SimpleDataclass(x=10, y=20)
    assert llm_mod._asdict_shallow(d) == {"x": 10, "y": 20}
    
    # Random object
    class X:
        def __init__(self):
            self.z = 99
    assert llm_mod._asdict_shallow(X()) == {"z": 99}
    
    # Failures
    assert llm_mod._asdict_shallow("string") is None
    assert llm_mod._asdict_shallow(None) is None

def test_get_helper():
    d = {"a": 1}
    o = SimpleNamespace(a=1)
    assert llm_mod._get(d, "a") == 1
    assert llm_mod._get(o, "a") == 1
    assert llm_mod._get(d, "b", 2) == 2
    assert llm_mod._get(o, "b", 2) == 2

def test_shape_preview():
    # Dict keys only
    huge_dict = {f"k{i}": i for i in range(50)}
    preview = llm_mod._shape_preview(huge_dict)
    assert "_keys" in preview
    assert "k0" in preview
    assert len(preview) < 1000

    # List truncation
    huge_list = [i for i in range(50)]
    preview_l = llm_mod._shape_preview(huge_list)
    assert "[0, 1]" in preview_l or "[0, 1" in preview_l # check approximate json dump

    # Exception safety
    class Unpreviewable:
        def __str__(self):
            raise ValueError("No")
    assert llm_mod._shape_preview(Unpreviewable()) == "<unpreviewable>"

# ---------------------------------------------------------------------
# Text -> JSON Parsing Tests
# ---------------------------------------------------------------------

def test_strip_code_fences():
    # Standard markdown
    s1 = "```json\n{\"a\": 1}\n```"
    assert llm_mod._strip_code_fences(s1) == "{\"a\": 1}"
    
    # Case insensitive / no type
    s2 = "```\n[1, 2]\n```"
    assert llm_mod._strip_code_fences(s2) == "[1, 2]"
    
    # Whitespace around
    # Note: The regex strictly expects the closing fence to start the line.
    s3 = "  ```json  \n  {\"x\": 1}  \n```  "
    assert llm_mod._strip_code_fences(s3) == "{\"x\": 1}"
    
    # No fences
    s4 = "{\"x\": 1}"
    assert llm_mod._strip_code_fences(s4) == "{\"x\": 1}"

def test_extract_balanced_block():
    s = "Text before {\"a\": {\"b\": \"}\"}, \"c\": [1]} text after"
    # logic: find first {, scan until balanced }
    start = s.find("{")
    extracted = llm_mod._extract_balanced_block(s, start, "{", "}")
    expected = "{\"a\": {\"b\": \"}\"}, \"c\": [1]}"
    assert extracted == expected

def test_extract_balanced_block_escaped_quotes():
    # Ensure logic doesn't think \" is a quote closer
    s = r'{"key": "value with \" quote inside"} post'
    start = 0
    extracted = llm_mod._extract_balanced_block(s, start, "{", "}")
    assert extracted == r'{"key": "value with \" quote inside"}'

def test_find_first_balanced_json():
    # Array
    s1 = "Sure! [1, 2, 3] is the answer."
    assert llm_mod._find_first_balanced_json(s1) == "[1, 2, 3]"
    
    # Object
    s2 = "Output: {\"x\": 1}"
    assert llm_mod._find_first_balanced_json(s2) == "{\"x\": 1}"
    
    # Nested
    s3 = "garbage {{{}}} garbage"
    # The finder looks for first { or [. It should grab the outer {..}
    assert llm_mod._find_first_balanced_json(s3) == "{{{}}}"

    # No JSON
    assert llm_mod._find_first_balanced_json("No json here") is None

def test_parse_json_from_text():
    # 1. Direct valid
    assert llm_mod._parse_json_from_text('{"a":1}') == {"a": 1}
    
    # 2. Fenced
    assert llm_mod._parse_json_from_text('```json\n{"a":1}\n```') == {"a": 1}
    
    # 3. Embedded
    assert llm_mod._parse_json_from_text('Here is logic: {"a":1}') == {"a": 1}
    
    # 4. Malformed
    assert llm_mod._parse_json_from_text('{a: 1}') is None  # invalid json
    assert llm_mod._parse_json_from_text('') is None
    assert llm_mod._parse_json_from_text(None) is None

# ---------------------------------------------------------------------
# Harvest / Extraction Tests
# ---------------------------------------------------------------------

def test_collect_text_parts_complex_structure():
    """
    Test extraction from the complex nested structures LiteLLM returns,
    including mixed dicts and objects.
    """
    # Construct a mess mimicking LiteLLM Responses API
    # output[0] -> object with .content list
    # content[0] -> object with .text
    # content[1] -> dict with "text"
    
    part_obj = SimpleNamespace(type="text", text="part_obj_text")
    part_dict = {"type": "text", "text": "part_dict_text"}
    
    item_obj = SimpleNamespace(content=[part_obj, part_dict])
    
    resp = SimpleNamespace(
        output=[item_obj],
        output_text="top_level_text"
    )
    
    candidates = llm_mod._collect_text_parts(resp)
    
    # Order matters in the implementation: Items -> TopLevel -> Legacy
    assert "part_obj_text" in candidates
    assert "part_dict_text" in candidates
    assert "top_level_text" in candidates

def test_collect_legacy_text():
    # Legacy choices[0].message.content
    resp = {
        "choices": [
            {"message": {"content": "legacy_content"}}
        ]
    }
    candidates = llm_mod._collect_text_parts(resp)
    assert "legacy_content" in candidates

def test_extract_structured_preparsed():
    # 1. output_parsed (top level)
    r1 = {"output_parsed": {"res": 1}}
    assert llm_mod._extract_structured(r1) == {"res": 1}
    
    # 2. output[].parsed (item level)
    r2 = SimpleNamespace(output=[{"parsed": {"res": 2}}])
    assert llm_mod._extract_structured(r2) == {"res": 2}
    
    # 3. output[].content[].parsed (part level)
    r3 = SimpleNamespace(output=[
        SimpleNamespace(content=[
            {"parsed": {"res": 3}}
        ])
    ])
    assert llm_mod._extract_structured(r3) == {"res": 3}

def test_extract_structured_text_fallback():
    # No parsed fields, but valid JSON in text
    resp = SimpleNamespace(output_text='{"res": 4}')
    assert llm_mod._extract_structured(resp) == {"res": 4}
    
    # With Validator: ensures we pick the valid one if multiple exist
    # (Simulate multiple text candidates where first is junk, second is valid)
    resp_multi = SimpleNamespace(
        output=[{"content": [{"text": "not json"}]}],
        output_text='{"name": "found", "age": 99}'
    )
    adapter = TypeAdapter(SimpleModel)
    result = llm_mod._extract_structured(resp_multi, validator=adapter)
    assert result == {"name": "found", "age": 99}

# ---------------------------------------------------------------------
# Payload / Schema Tests
# ---------------------------------------------------------------------

def test_messages_to_input():
    msgs = [
        SystemMessage(content="sys"),
        HumanMessage(content="user"),
        AIMessage(content="ai"),
        {"role": "user", "content": "dict_user"},
        SimpleNamespace(type="human", content="obj_user")
    ]
    res = llm_mod._messages_to_input(msgs)
    assert res == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "user"},
        {"role": "assistant", "content": "ai"},
        {"role": "user", "content": "dict_user"},
        {"role": "user", "content": "obj_user"},
    ]
    
    # Fallback
    assert llm_mod._messages_to_input("not_list")[0]["role"] == "user"

def test_schema_envelope_and_build_format():
    # 1. Explicit dictionary passed
    rf_dict = {"type": "json_schema", "json_schema": {"name": "x"}}
    assert llm_mod._build_response_format(tool_name="t", response_model=SimpleModel, response_format=rf_dict) == rf_dict

    # 2. Pydantic Model derivation
    rf = llm_mod._build_response_format(tool_name="t", response_model=SimpleModel, response_format=None)
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["name"] == "SimpleModel"
    assert "properties" in rf["json_schema"]["schema"]

    # 3. TypeAdapter derivation
    ta = TypeAdapter(List[int])
    rf_ta = llm_mod._build_response_format(tool_name="list_tool", response_model=ta, response_format=None)
    assert rf_ta["json_schema"]["name"] == "list_tool" # Falls back to tool_name

def test_apply_text_params():
    p = {}
    llm_mod._apply_text_params_top_level(p, {"temperature": 0.7, "invalid": 1})
    assert p["temperature"] == 0.7
    assert "invalid" not in p

def test_is_reasoning_model():
    # "o3" is explicitly in REASONING_MODEL_PREFIXES in the source code
    assert llm_mod._is_reasoning_model("o3-mini") is True
    
    # "gpt-5" is also in the list
    assert llm_mod._is_reasoning_model("gpt-5-something") is True
    
    # "gpt-4" is NOT in the list
    assert llm_mod._is_reasoning_model("gpt-4") is False
    
    # "o1" is NOT in the list provided in the source file
    assert llm_mod._is_reasoning_model("o1-preview") is False

# ---------------------------------------------------------------------
# Service Class Tests (Main Logic)
# ---------------------------------------------------------------------

@pytest.fixture
def service():
    return llm_mod.LLMService()

@pytest.fixture
def mock_litellm(monkeypatch):
    """Patch litellm.responses and asyncio.to_thread to run synchronously."""
    
    response_container = {"resp": None, "kwargs": None, "raise_error": None}

    def _sync_responses(**kwargs):
        response_container["kwargs"] = kwargs
        if response_container["raise_error"]:
            raise response_container["raise_error"]
        return response_container["resp"]

    async def _fake_to_thread(func, **kwargs):
        return func(**kwargs)

    monkeypatch.setattr(asyncio, "to_thread", _fake_to_thread)
    monkeypatch.setattr(llm_mod.litellm, "responses", _sync_responses)
    
    return response_container

@pytest.mark.asyncio
async def test_service_happy_path_parsed(service, mock_litellm):
    # Setup: LiteLLM returns a response object with output_parsed
    mock_litellm["resp"] = SimpleNamespace(output_parsed={"name": "Test", "age": 30})
    
    res = await service.get_structured_response(
        tool_name="test",
        messages=[],
        response_model=SimpleModel
    )
    
    assert isinstance(res, SimpleModel)
    assert res.name == "Test"
    assert res.age == 30
    
    # Verify payload defaults
    payload = mock_litellm["kwargs"]
    assert payload["model"] == service.default_model
    assert payload["tool_choice"] == "none"
    assert "response_format" in payload

@pytest.mark.asyncio
async def test_service_reasoning_model_payload(service, mock_litellm):
    mock_litellm["resp"] = SimpleNamespace(output_parsed={"name": "R", "age": 1})
    
    await service.get_structured_response(
        tool_name="t", 
        messages=[], 
        response_model=SimpleModel,
        model="o3-mini",
        reasoning={"effort": "high"},
        text_params={"temperature": 0.9} # Should be ignored/not top-level for reasoning models
    )
    
    payload = mock_litellm["kwargs"]
    assert payload["model"] == "o3-mini"
    assert "reasoning" in payload
    assert payload["reasoning"]["effort"] == "high"
    # Ensure temperature didn't pollute top-level for reasoning model
    assert "temperature" not in payload

@pytest.mark.asyncio
async def test_service_text_fallback_parsing(service, mock_litellm):
    # Setup: No parsed field, but valid text
    text_json = '```json\n{"name": "Fallback", "age": 5}\n```'
    mock_litellm["resp"] = SimpleNamespace(output_text=text_json)
    
    res = await service.get_structured_response(
        tool_name="t",
        messages=[],
        response_model=SimpleModel
    )
    
    assert res.name == "Fallback"

@pytest.mark.asyncio
async def test_service_validation_error(service, mock_litellm):
    # Setup: JSON valid, but Schema invalid (age is string)
    mock_litellm["resp"] = SimpleNamespace(output_parsed={"name": "Bad", "age": "not_int"})
    
    with pytest.raises(llm_mod.StructuredOutputError) as exc:
        await service.get_structured_response(
            tool_name="t",
            messages=[],
            response_model=SimpleModel
        )
    
    # The exception message usually contains "validation failed"
    assert "validation failed" in str(exc.value)
    
    # The 'preview' logic converts dicts to `{"_keys": [...]}` to hide values.
    # So we verify that it correctly previewed the keys (and not the value 'Bad').
    assert "_keys" in str(exc.value.preview)
    assert "name" in str(exc.value.preview)

@pytest.mark.asyncio
async def test_service_no_json_found(service, mock_litellm):
    # Setup: Just chatter, no JSON
    mock_litellm["resp"] = SimpleNamespace(output_text="I cannot do that.")
    
    with pytest.raises(llm_mod.StructuredOutputError) as exc:
        await service.get_structured_response(
            tool_name="t",
            messages=[],
            response_model=SimpleModel
        )
    assert "Could not locate/parse JSON" in str(exc.value)

@pytest.mark.asyncio
async def test_service_api_exception(service, mock_litellm):
    mock_litellm["raise_error"] = RuntimeError("LiteLLM Down")
    
    with pytest.raises(RuntimeError, match="LiteLLM Down"):
        await service.get_structured_response(
            tool_name="t",
            messages=[],
            response_model=SimpleModel
        )

@pytest.mark.asyncio
async def test_service_missing_schema(service, monkeypatch):
    """
    Force _build_response_format to return None so we can verify the
    'missing schema' error path is strictly taken.
    """
    monkeypatch.setattr(llm_mod, "_build_response_format", lambda **kwargs: None)

    with pytest.raises(llm_mod.StructuredOutputError, match="requires a valid JSON Schema"):
        await service.get_structured_response(
            tool_name="t",
            messages=[],
            response_model=None # This value is irrelevant due to the monkeypatch
        )

@pytest.mark.asyncio
async def test_service_raw_dict_response_logging(service, mock_litellm):
    """Verify the service handles a raw dict response (legacy style) without crashing logging."""
    mock_litellm["resp"] = {"output_parsed": {"name": "Dict", "age": 1}, "id": "123"}
    
    res = await service.get_structured_response(
        tool_name="t", messages=[], response_model=SimpleModel
    )
    assert res.name == "Dict"

@pytest.mark.asyncio
async def test_service_metadata_merging(service, mock_litellm):
    mock_litellm["resp"] = SimpleNamespace(output_parsed={"name": "M", "age": 1})
    
    await service.get_structured_response(
        tool_name="t",
        messages=[],
        response_model=SimpleModel,
        trace_id="tid",
        session_id="sid",
        metadata={"custom": "val"}
    )
    
    meta = mock_litellm["kwargs"]["metadata"]
    assert meta["trace_id"] == "tid"
    assert meta["session_id"] == "sid"
    assert meta["custom"] == "val"
    assert meta["tool"] == "t"