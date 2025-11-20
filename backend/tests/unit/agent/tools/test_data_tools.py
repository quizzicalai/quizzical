# tests/unit/agent/tools/test_data_tools.py

import pytest
from unittest.mock import MagicMock, AsyncMock
from types import SimpleNamespace
from typing import Optional

# Import the module under test
from app.agent.tools import data_tools as dtools

# Import config to patch settings
from app.core.config import settings

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_budget_counter():
    """Reset the global in-memory budget counter before every test."""
    dtools._RETRIEVAL_BUDGET.clear()
    yield
    dtools._RETRIEVAL_BUDGET.clear()

@pytest.fixture
def mock_settings(monkeypatch):
    """
    Helper to patch `settings.retrieval` dynamically.
    Returns the configuration object for verification if needed.
    """
    def _configure(
        policy="all",
        allow_wiki=True,
        allow_web=True,
        max_calls=5,
        allowed_domains: Optional[list] = None
    ):
        r_settings = SimpleNamespace(
            policy=policy,
            allow_wikipedia=allow_wiki,
            allow_web=allow_web,
            max_calls_per_run=max_calls,
            allowed_domains=allowed_domains or []
        )
        monkeypatch.setattr(settings, "retrieval", r_settings, raising=False)
        return r_settings
    return _configure

@pytest.fixture
def mock_llm_tools_config(monkeypatch):
    """
    Helper to patch `settings.llm_tools` (specifically 'web_search') 
    and `settings.llm.provider` for the web search tool.
    """
    def _configure(
        model="gpt-4o",
        allowed_domains=None,
        provider="openai",
        search_context_size="small",
        effort="medium",
        user_location=None
    ):
        # Patch LLM provider
        monkeypatch.setattr(settings, "llm", SimpleNamespace(provider=provider), raising=False)

        # Patch Tool Config
        cfg = SimpleNamespace(
            model=model,
            allowed_domains=allowed_domains or [],
            effort=effort,
            tool_choice="auto",
            search_context_size=search_context_size,
            user_location=user_location
        )
        monkeypatch.setattr(settings, "llm_tools", {"web_search": cfg}, raising=False)
        return cfg
    return _configure


# ---------------------------------------------------------------------------
# 1. Policy & Budget Logic Tests
# ---------------------------------------------------------------------------

def test_policy_allows_defaults(monkeypatch):
    """If settings.retrieval is missing, allow by default (back-compat)."""
    monkeypatch.delattr(settings, "retrieval", raising=False)
    assert dtools._policy_allows("web") is True
    assert dtools._policy_allows("wiki") is True

def test_policy_allows_off(mock_settings):
    mock_settings(policy="off")
    assert dtools._policy_allows("web") is False
    assert dtools._policy_allows("wiki") is False

def test_policy_allows_selective(mock_settings):
    mock_settings(policy="custom", allow_wiki=True, allow_web=False)
    assert dtools._policy_allows("wiki") is True
    assert dtools._policy_allows("web") is False

def test_policy_allows_media_only(mock_settings):
    mock_settings(policy="media_only")
    # policy='media_only' is conservative: strictly requires is_media=True
    assert dtools._policy_allows("web", is_media=True) is True
    assert dtools._policy_allows("web", is_media=False) is False
    assert dtools._policy_allows("web", is_media=None) is False

def test_consume_retrieval_slot_logic(mock_settings):
    mock_settings(max_calls=2)
    tid, sid = "trace_1", "session_1"
    key = dtools._run_key(tid, sid)

    # 1. First call (Usage 0 -> 1)
    assert dtools.consume_retrieval_slot(tid, sid) is True
    assert dtools._RETRIEVAL_BUDGET[key] == 1

    # 2. Second call (Usage 1 -> 2)
    assert dtools.consume_retrieval_slot(tid, sid) is True
    assert dtools._RETRIEVAL_BUDGET[key] == 2

    # 3. Third call (Usage 2 >= max_calls -> Block)
    assert dtools.consume_retrieval_slot(tid, sid) is False
    assert dtools._RETRIEVAL_BUDGET[key] == 2  # Should not increment

def test_consume_retrieval_slot_no_limits_if_config_missing(monkeypatch):
    monkeypatch.delattr(settings, "retrieval", raising=False)
    assert dtools.consume_retrieval_slot("t", "s") is True
    # Should not have touched the budget dict
    assert len(dtools._RETRIEVAL_BUDGET) == 0


# ---------------------------------------------------------------------------
# 2. Wikipedia Search Tool Tests
# ---------------------------------------------------------------------------

def test_wikipedia_search_blocked_by_policy(mock_settings, monkeypatch):
    mock_settings(allow_wiki=False)
    
    # Mock implementation to ensure it's NOT called
    mock_impl = MagicMock()
    monkeypatch.setattr(dtools, "_wikipedia_search", mock_impl)

    result = dtools.wikipedia_search.invoke("Python")
    assert result == ""
    mock_impl.run.assert_not_called()

def test_wikipedia_search_blocked_by_zero_budget(mock_settings, monkeypatch):
    """Wikipedia search has a coarse check: if max_calls <= 0, it blocks globally."""
    mock_settings(allow_wiki=True, max_calls=0)
    
    mock_impl = MagicMock()
    monkeypatch.setattr(dtools, "_wikipedia_search", mock_impl)

    result = dtools.wikipedia_search.invoke("Python")
    assert result == ""
    mock_impl.run.assert_not_called()

def test_wikipedia_search_success(mock_settings, monkeypatch):
    mock_settings(allow_wiki=True, max_calls=10)
    
    mock_impl = MagicMock()
    mock_impl.run.return_value = "Python is a programming language."
    monkeypatch.setattr(dtools, "_wikipedia_search", mock_impl)

    result = dtools.wikipedia_search.invoke("Python")
    assert result == "Python is a programming language."

def test_wikipedia_search_exception_handling(mock_settings, monkeypatch):
    mock_settings(allow_wiki=True)
    
    mock_impl = MagicMock()
    mock_impl.run.side_effect = Exception("Wikipedia API Down")
    monkeypatch.setattr(dtools, "_wikipedia_search", mock_impl)

    # Should return empty string, not raise
    result = dtools.wikipedia_search.invoke("Python")
    assert result == ""


# ---------------------------------------------------------------------------
# 3. Web Search Tool Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_web_search_blocked_by_policy(mock_settings, monkeypatch):
    mock_settings(allow_web=False)
    monkeypatch.setattr(dtools.llm_service, "get_text", AsyncMock())
    
    result = await dtools.web_search.ainvoke("cats")
    assert result == ""
    assert dtools.llm_service.get_text.await_count == 0

@pytest.mark.asyncio
async def test_web_search_blocked_by_budget(mock_settings, monkeypatch):
    mock_settings(allow_web=True, max_calls=1)
    
    # Pre-consume the only slot
    dtools.consume_retrieval_slot("t1", "s1")
    
    monkeypatch.setattr(dtools.llm_service, "get_text", AsyncMock())

    result = await dtools.web_search.ainvoke({"query": "cats", "trace_id": "t1", "session_id": "s1"})
    
    assert result == ""
    assert dtools.llm_service.get_text.await_count == 0

@pytest.mark.asyncio
async def test_web_search_no_tool_config(mock_settings, monkeypatch):
    """Fail safely if settings.llm_tools.web_search is missing."""
    mock_settings(allow_web=True)
    monkeypatch.setattr(settings, "llm_tools", {}, raising=False)
    
    result = await dtools.web_search.ainvoke("cats")
    assert result == ""

@pytest.mark.asyncio
async def test_web_search_happy_path_openai(mock_settings, mock_llm_tools_config, monkeypatch):
    """
    Verify successful execution with OpenAI provider:
    - Checks tool_spec construction (domains)
    - Checks metadata propagation
    """
    mock_settings(allow_web=True, allowed_domains=["global-allowed.com"])
    mock_llm_tools_config(
        provider="openai",
        allowed_domains=["local-ignored.com"], # Global should override this
        model="gpt-4-search"
    )

    mock_get_text = AsyncMock(return_value="Search result content")
    monkeypatch.setattr(dtools.llm_service, "get_text", mock_get_text)

    # Execute
    trace_id, session_id = "t-123", "s-456"
    result = await dtools.web_search.ainvoke({
        "query": "latest python features",
        "trace_id": trace_id,
        "session_id": session_id
    })

    assert result == "Search result content"

    # Verify LLM call arguments
    call_kwargs = mock_get_text.call_args.kwargs
    
    # 1. Metadata
    assert call_kwargs["metadata"] == {"trace_id": trace_id, "session_id": session_id}
    
    # 2. Tool Spec
    tools = call_kwargs["tools"]
    assert len(tools) == 1
    spec = tools[0]
    assert spec["type"] == "web_search"
    # Global settings domains take precedence over tool config
    assert spec["filters"]["allowed_domains"] == ["global-allowed.com"]

    # 3. Options
    # For OpenAI, search_context_size is NOT passed via web_search_options
    assert "web_search_options" not in call_kwargs

@pytest.mark.asyncio
async def test_web_search_non_openai_provider(mock_settings, mock_llm_tools_config, monkeypatch):
    """
    Verify behavior for non-OpenAI providers (e.g., Anthropic/Perplexity):
    - Tool type should be 'web_search_preview'
    - search_context_size should be passed in extra options
    """
    mock_settings(allow_web=True)
    mock_llm_tools_config(
        provider="anthropic",
        search_context_size="large"
    )

    mock_get_text = AsyncMock(return_value="Result")
    monkeypatch.setattr(dtools.llm_service, "get_text", mock_get_text)

    await dtools.web_search.ainvoke("query")

    call_kwargs = mock_get_text.call_args.kwargs
    spec = call_kwargs["tools"][0]

    # Check adjusted tool type
    assert spec["type"] == "web_search_preview"
    
    # Check extra options injection
    assert "web_search_options" in call_kwargs
    assert call_kwargs["web_search_options"]["search_context_size"] == "large"

@pytest.mark.asyncio
async def test_web_search_user_location(mock_settings, mock_llm_tools_config, monkeypatch):
    """Verify user_location is injected into tool spec if present in config."""
    mock_settings(allow_web=True)
    
    # Create a fake location object with a model_dump method
    location_obj = SimpleNamespace(model_dump=lambda: {"city": "Paris", "country": "FR"})
    
    mock_llm_tools_config(user_location=location_obj)
    monkeypatch.setattr(dtools.llm_service, "get_text", AsyncMock(return_value="ok"))

    await dtools.web_search.ainvoke("query")
    
    call_kwargs = dtools.llm_service.get_text.call_args.kwargs
    spec = call_kwargs["tools"][0]
    
    assert "user_location" in spec
    assert spec["user_location"]["type"] == "approximate"
    assert spec["user_location"]["city"] == "Paris"