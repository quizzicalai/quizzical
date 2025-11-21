import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from pydantic import BaseModel

# Module under test
from app.agent import llm_helpers
# We don't import settings from core.config here because we want to completely 
# replace the one inside llm_helpers with a mock.

# ---------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------

class MockResult(BaseModel):
    data: str

class MockLLMService:
    def __init__(self):
        self.calls = []
        self.return_value = MockResult(data="success")
        self.raise_error = None

    async def get_structured_response(
        self,
        tool_name: str,
        messages: Any,
        response_model: Any,
        **kwargs
    ):
        self.calls.append({
            "tool_name": tool_name,
            "messages": messages,
            "response_model": response_model,
            "kwargs": kwargs
        })
        if self.raise_error:
            raise self.raise_error
        return self.return_value

# ---------------------------------------------------------------------
# Internal Helper Function Tests
# ---------------------------------------------------------------------

def test_safe_len():
    assert llm_helpers._safe_len([1, 2, 3]) == 3
    assert llm_helpers._safe_len("abc") == 3
    assert llm_helpers._safe_len(None) is None
    assert llm_helpers._safe_len(123) is None  # Int has no len()

def test_cfg_get():
    # Dict access
    d = {"a": 1}
    assert llm_helpers._cfg_get(d, "a") == 1
    assert llm_helpers._cfg_get(d, "b", "default") == "default"

    # Object access
    o = SimpleNamespace(x=10)
    assert llm_helpers._cfg_get(o, "x") == 10
    assert llm_helpers._cfg_get(o, "y", 99) == 99

    # None handling
    assert llm_helpers._cfg_get(None, "anything") is None

    # Exception safety (e.g. getattr fails strictly)
    class Broken:
        @property
        def bad(self):
            raise ValueError("oops")
    
    # Depending on implementation, accessing a property that raises might bubble up 
    # or be caught. The function catches Exception and returns default.
    assert llm_helpers._cfg_get(Broken(), "bad", "def") == "def"

def test_deep_get():
    data = {
        "a": {
            "b": SimpleNamespace(c=100)
        }
    }
    
    # Happy path mixed dict/object
    assert llm_helpers._deep_get(data, ["a", "b", "c"]) == 100
    
    # Missing intermediate
    assert llm_helpers._deep_get(data, ["a", "x", "c"], "miss") == "miss"
    
    # None intermediate
    data_none = {"a": None}
    assert llm_helpers._deep_get(data_none, ["a", "b"], "default") == "default"
    
    # Exception during traversal
    # (reusing Broken class logic essentially covered by the try/except block)
    assert llm_helpers._deep_get("not_dict", ["key"], "def") == "def"

def test_get_tool_cfg(monkeypatch):
    """
    Verify the priority list:
    1. settings.llm_tools[name]
    2. settings.llm.tools[name]
    3. settings.quizzical.llm.tools[name]
    
    We patch llm_helpers.settings directly to a plain object so we can attach arbitrary 
    attributes without fighting Pydantic validation.
    """
    mock_settings = SimpleNamespace()
    monkeypatch.setattr(llm_helpers, "settings", mock_settings)
    
    # Case 1: settings.llm_tools (Direct dictionary)
    mock_settings.llm_tools = {"test_tool": {"model": "gpt-1"}}
    assert llm_helpers._get_tool_cfg("test_tool") == {"model": "gpt-1"}
    
    # Cleanup for next assertion
    delattr(mock_settings, "llm_tools")
    
    # Case 2: settings.llm.tools (Object -> Dict)
    class LLMConfig:
        tools = {"test_tool": {"model": "gpt-2"}}
    
    mock_settings.llm = LLMConfig()
    assert llm_helpers._get_tool_cfg("test_tool") == {"model": "gpt-2"}
    
    # Cleanup
    delattr(mock_settings, "llm")
    
    # Case 3: settings.quizzical.llm.tools (Deep object nesting)
    class QuizzicalConfig:
        class InnerLLM:
            tools = {"test_tool": {"model": "gpt-3"}}
        llm = InnerLLM()
        
    mock_settings.quizzical = QuizzicalConfig()
    assert llm_helpers._get_tool_cfg("test_tool") == {"model": "gpt-3"}
    
    # Case 4: Not found
    delattr(mock_settings, "quizzical")
    assert llm_helpers._get_tool_cfg("test_tool") is None

# ---------------------------------------------------------------------
# invoke_structured Tests
# ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invoke_structured_happy_path(monkeypatch):
    """Test standard invocation passing through to llm_service."""
    mock_service = MockLLMService()
    monkeypatch.setattr(llm_helpers, "llm_service", mock_service)
    
    # Mock settings logic by replacing the helper function directly
    # This avoids needing to set up complex settings structures
    monkeypatch.setattr(llm_helpers, "_get_tool_cfg", lambda name: {
        "model": "gpt-4o",
        "temperature": 0.7,
        "max_output_tokens": 100,
        "timeout_s": 50
    })

    result = await llm_helpers.invoke_structured(
        tool_name="my_tool",
        messages=[{"role": "user", "content": "hi"}],
        response_model=MockResult,
        session_id="sess-123"
    )

    assert isinstance(result, MockResult)
    assert result.data == "success"
    
    assert len(mock_service.calls) == 1
    call = mock_service.calls[0]
    
    # Check arg passing
    assert call["tool_name"] == "my_tool"
    assert call["kwargs"]["model"] == "gpt-4o"
    assert call["kwargs"]["max_output_tokens"] == 100
    assert call["kwargs"]["timeout_s"] == 50
    assert call["kwargs"]["session_id"] == "sess-123"
    
    # Check text_params construction from temperature
    assert call["kwargs"]["text_params"] == {"temperature": 0.7}
    assert call["kwargs"]["reasoning"] is None

@pytest.mark.asyncio
async def test_invoke_structured_reasoning_params(monkeypatch):
    """Test mapping of 'effort' to reasoning dict."""
    mock_service = MockLLMService()
    monkeypatch.setattr(llm_helpers, "llm_service", mock_service)
    
    monkeypatch.setattr(llm_helpers, "_get_tool_cfg", lambda name: {
        "model": "o1-preview",
        "effort": "high"
    })

    await llm_helpers.invoke_structured(
        tool_name="reasoning_tool",
        messages=[],
        response_model=MockResult
    )

    call = mock_service.calls[0]
    assert call["kwargs"]["reasoning"] == {"effort": "high"}
    # temperature was None in config, so text_params should be None
    assert call["kwargs"]["text_params"] is None

@pytest.mark.asyncio
async def test_invoke_structured_error_propagation(monkeypatch, caplog):
    """Ensure errors are logged and re-raised."""
    mock_service = MockLLMService()
    mock_service.raise_error = ValueError("Simulated LLM Failure")
    monkeypatch.setattr(llm_helpers, "llm_service", mock_service)
    monkeypatch.setattr(llm_helpers, "_get_tool_cfg", lambda name: {})

    with pytest.raises(ValueError, match="Simulated LLM Failure"):
        await llm_helpers.invoke_structured(
            tool_name="fail_tool",
            messages=[],
            response_model=MockResult
        )
    
    # Check logs
    assert "llm.invoke_structured.fail" in caplog.text
    assert "Simulated LLM Failure" in caplog.text

@pytest.mark.asyncio
async def test_invoke_structured_schema_kwargs_warning(monkeypatch, caplog):
    """Ensure schema_kwargs (deprecated/ignored) triggers a debug log."""
    mock_service = MockLLMService()
    monkeypatch.setattr(llm_helpers, "llm_service", mock_service)
    monkeypatch.setattr(llm_helpers, "_get_tool_cfg", lambda name: {})
    
    # Enable capturing debug logs
    import logging
    caplog.set_level(logging.DEBUG)

    await llm_helpers.invoke_structured(
        tool_name="tool",
        messages=[],
        response_model=MockResult,
        schema_kwargs={"title": "My Schema"}
    )

    assert "llm.invoke_structured.schema_kwargs.ignored" in caplog.text

@pytest.mark.asyncio
async def test_invoke_structured_instance_check(monkeypatch):
    """Verify the post-execution instance check doesn't crash execution if passed."""
    mock_service = MockLLMService()
    monkeypatch.setattr(llm_helpers, "llm_service", mock_service)
    monkeypatch.setattr(llm_helpers, "_get_tool_cfg", lambda name: {})

    # The mock returns an instance of MockResult.
    # We ask for MockResult, so it should pass the check.
    res = await llm_helpers.invoke_structured(
        tool_name="t", messages=[], response_model=MockResult
    )
    assert isinstance(res, MockResult)

    # Now assume LLM service returned a DICT (common in some mock setups or raw modes),
    # but we asked for a MODEL. The check inside helper wraps in try/except.
    # We want to ensure it doesn't raise an unhandled exception even if assertion fails inside the try block.
    mock_service.return_value = {"data": "not a model"} 
    
    # This technically violates the type hint of invoke_structured return, 
    # but at runtime Python is dynamic. 
    # The helper has a `try: assert ... except: pass` block.
    res_dict = await llm_helpers.invoke_structured(
        tool_name="t", messages=[], response_model=MockResult
    )
    assert res_dict == {"data": "not a model"}