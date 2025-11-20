# tests/unit/agent/tools/test_data_tools.py

import pytest
from unittest.mock import MagicMock, AsyncMock
from types import SimpleNamespace
from typing import Optional, Any

# Import the module under test
from app.agent.tools import data_tools as dtools

# Import config to patch settings
from app.core.config import settings

# ---------------------------------------------------------------------------
# Alignment Configuration
# ---------------------------------------------------------------------------

# 1. Unit test marker (fast, no external networks)
# 2. No Tool Stubs marker: tells fixtures NOT to replace dtools.web_search
#    with empty mocks, so we can test the real function logic.
pytestmark = [pytest.mark.unit, pytest.mark.no_tool_stubs]


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
    """
    def _configure(
        policy="all",
        allow_wiki=True,
        allow_web=True,
        max_calls: Any = 5,
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
    Helper to patch `settings.llm_tools` and `settings.llm`.
    """
    def _configure(
        model="gpt-4o",
        allowed_domains=None,
        provider="openai",
        search_context_size="small",
        effort="medium",
        user_location=None,
        tool_choice="auto"
    ):
        # Patch LLM provider
        monkeypatch.setattr(settings, "llm", SimpleNamespace(provider=provider), raising=False)

        # Patch Tool Config
        cfg = SimpleNamespace(
            model=model,
            allowed_domains=allowed_domains or [],
            effort=effort,
            tool_choice=tool_choice,
            search_context_size=search_context_size,
            user_location=user_location
        )
        monkeypatch.setattr(settings, "llm_tools", {"web_search": cfg}, raising=False)
        return cfg
    return _configure


# ---------------------------------------------------------------------------
# 1. Helper Function Tests
# ---------------------------------------------------------------------------

def test_run_key_combinations():
    """Verify string formatting for cache keys."""
    assert dtools._run_key("t1", "s1") == "t1|s1"
    assert dtools._run_key(None, "s1") == "|s1"
    assert dtools._run_key("t1", None) == "t1|"
    assert dtools._run_key(None, None) == "|"

def test_policy_allows_defaults(monkeypatch):
    """If settings.retrieval is missing, allow by default."""
    monkeypatch.delattr(settings, "retrieval", raising=False)
    assert dtools._policy_allows("web") is True

def test_policy_allows_when_policy_is_none(mock_settings):
    mock_settings(policy=None)
    assert dtools._policy_allows("web") is False

def test_policy_allows_off(mock_settings):
    mock_settings(policy="off")
    assert dtools._policy_allows("web") is False
    assert dtools._policy_allows("wiki") is False

def test_policy_allows_granular_flags(mock_settings):
    mock_settings(policy="custom", allow_wiki=True, allow_web=False)
    assert dtools._policy_allows("wiki") is True
    assert dtools._policy_allows("web") is False

    mock_settings(policy="custom", allow_wiki=False, allow_web=True)
    assert dtools._policy_allows("wiki") is False
    assert dtools._policy_allows("web") is True

def test_policy_allows_media_only(mock_settings):
    mock_settings(policy="media_only")
    assert dtools._policy_allows("web", is_media=True) is True
    assert dtools._policy_allows("web", is_media=False) is False
    assert dtools._policy_allows("web", is_media=None) is False

def test_consume_retrieval_slot_missing_config(monkeypatch):
    monkeypatch.delattr(settings, "retrieval", raising=False)
    assert dtools.consume_retrieval_slot("t", "s") is True

def test_consume_retrieval_slot_garbage_config(mock_settings):
    mock_settings(max_calls="not_a_number") 
    assert dtools.consume_retrieval_slot("t", "s") is False

def test_consume_retrieval_slot_zero_limit(mock_settings):
    mock_settings(max_calls=0)
    assert dtools.consume_retrieval_slot("t", "s") is False

def test_consume_retrieval_slot_budget_enforcement(mock_settings):
    mock_settings(max_calls=2)
    key = dtools._run_key("t", "s")
    
    assert dtools.consume_retrieval_slot("t", "s") is True
    assert dtools._RETRIEVAL_BUDGET[key] == 1
    
    assert dtools.consume_retrieval_slot("t", "s") is True
    assert dtools._RETRIEVAL_BUDGET[key] == 2
    
    assert dtools.consume_retrieval_slot("t", "s") is False
    assert dtools._RETRIEVAL_BUDGET[key] == 2


# ---------------------------------------------------------------------------
# 2. Pydantic Model Tests
# ---------------------------------------------------------------------------

def test_pydantic_models():
    syn = dtools.SynopsisInput(synopsis="test")
    assert syn.synopsis == "test"
    char = dtools.CharacterInput(character_id="123")
    assert char.character_id == "123"


# ---------------------------------------------------------------------------
# 3. Wikipedia Tool Tests
# ---------------------------------------------------------------------------

def test_wiki_blocked_by_policy(mock_settings):
    mock_settings(allow_wiki=False)
    assert dtools.wikipedia_search.invoke("foo") == ""

def test_wiki_blocked_by_zero_budget(mock_settings):
    mock_settings(allow_wiki=True, max_calls=0)
    assert dtools.wikipedia_search.invoke("foo") == ""

def test_wiki_blocked_by_budget_parse_error(mock_settings):
    mock_settings(allow_wiki=True, max_calls="invalid")
    assert dtools.wikipedia_search.invoke("foo") == ""

def test_wiki_success(mock_settings, monkeypatch):
    mock_settings(allow_wiki=True, max_calls=5)
    mock_wrapper = MagicMock()
    mock_wrapper.run.return_value = "Wiki content"
    monkeypatch.setattr(dtools, "_wikipedia_search", mock_wrapper)
    
    assert dtools.wikipedia_search.invoke("foo") == "Wiki content"

def test_wiki_exception(mock_settings, monkeypatch):
    mock_settings(allow_wiki=True)
    mock_wrapper = MagicMock()
    mock_wrapper.run.side_effect = Exception("Wiki Down")
    monkeypatch.setattr(dtools, "_wikipedia_search", mock_wrapper)
    
    assert dtools.wikipedia_search.invoke("foo") == ""


# ---------------------------------------------------------------------------
# 4. Web Search Tool Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_web_search_blocked_by_policy(mock_settings):
    mock_settings(allow_web=False)
    res = await dtools.web_search.ainvoke("q")
    assert res == ""

@pytest.mark.asyncio
async def test_web_search_no_budget(mock_settings):
    mock_settings(allow_web=True, max_calls=1)
    dtools.consume_retrieval_slot("t", "s")
    res = await dtools.web_search.ainvoke({"query": "q", "trace_id": "t", "session_id": "s"})
    assert res == ""

@pytest.mark.asyncio
async def test_web_search_missing_tool_config(mock_settings, monkeypatch):
    mock_settings(allow_web=True)
    monkeypatch.setattr(settings, "llm_tools", {}, raising=False)
    res = await dtools.web_search.ainvoke("q")
    assert res == ""

@pytest.mark.asyncio
async def test_web_search_full_flow_openai(mock_settings, mock_llm_tools_config, monkeypatch):
    mock_settings(allow_web=True, allowed_domains=["https://settings-override.com/"])
    mock_llm_tools_config(
        provider="openai",
        effort="high",
        tool_choice={"type": "function", "function": {"name": "web_search"}},
        allowed_domains=["config-ignored.com"]
    )
    
    mock_llm = AsyncMock()
    mock_llm.get_text.return_value = "Search Results"
    monkeypatch.setattr(dtools, "llm_service", mock_llm)

    res = await dtools.web_search.ainvoke({"query": "q", "trace_id": "t", "session_id": "s"})
    assert res == "Search Results"

    kwargs = mock_llm.get_text.call_args.kwargs
    assert kwargs["reasoning"] == {"effort": "high"}
    tool_spec = kwargs["tools"][0]
    assert tool_spec["type"] == "web_search"
    assert tool_spec["filters"]["allowed_domains"] == ["settings-override.com"]
    assert isinstance(kwargs["tool_choice"], dict)

@pytest.mark.asyncio
async def test_web_search_non_openai_context_size(mock_settings, mock_llm_tools_config, monkeypatch):
    mock_settings(allow_web=True)
    mock_llm_tools_config(
        provider="anthropic", 
        search_context_size="large",
        effort=None,
        tool_choice=None
    )

    mock_llm = AsyncMock(return_value="Res")
    monkeypatch.setattr(dtools, "llm_service", mock_llm)

    await dtools.web_search.ainvoke("q")

    kwargs = mock_llm.get_text.call_args.kwargs
    assert kwargs["tools"][0]["type"] == "web_search_preview"
    assert kwargs["web_search_options"] == {"search_context_size": "large"}
    assert kwargs["reasoning"] is None
    assert kwargs["tool_choice"] == "auto"

@pytest.mark.asyncio
async def test_web_search_config_domains_fallback(mock_settings, mock_llm_tools_config, monkeypatch):
    mock_settings(allow_web=True, allowed_domains=[])
    mock_llm_tools_config(allowed_domains=["config-domain.com"])

    mock_llm = AsyncMock(return_value="Res")
    monkeypatch.setattr(dtools, "llm_service", mock_llm)

    await dtools.web_search.ainvoke("q")
    
    tool_spec = mock_llm.get_text.call_args.kwargs["tools"][0]
    assert tool_spec["filters"]["allowed_domains"] == ["config-domain.com"]

@pytest.mark.asyncio
async def test_web_search_user_location(mock_settings, mock_llm_tools_config, monkeypatch):
    mock_settings(allow_web=True)
    loc_obj = SimpleNamespace(model_dump=lambda: {"city": "NYC", "country": "USA"})
    mock_llm_tools_config(user_location=loc_obj)

    mock_llm = AsyncMock(return_value="Res")
    monkeypatch.setattr(dtools, "llm_service", mock_llm)

    await dtools.web_search.ainvoke("q")

    tool_spec = mock_llm.get_text.call_args.kwargs["tools"][0]
    assert tool_spec["user_location"] == {"type": "approximate", "city": "NYC", "country": "USA"}

@pytest.mark.asyncio
async def test_web_search_user_location_fail_safe(mock_settings, mock_llm_tools_config, monkeypatch):
    mock_settings(allow_web=True)
    class BrokenLoc:
        def model_dump(self): raise ValueError("Explosion")
    mock_llm_tools_config(user_location=BrokenLoc())

    mock_llm = AsyncMock(return_value="Res")
    monkeypatch.setattr(dtools, "llm_service", mock_llm)

    await dtools.web_search.ainvoke("q")
    tool_spec = mock_llm.get_text.call_args.kwargs["tools"][0]
    assert "user_location" not in tool_spec

@pytest.mark.asyncio
async def test_web_search_api_exception(mock_settings, mock_llm_tools_config, monkeypatch):
    mock_settings(allow_web=True)
    mock_llm_tools_config()
    
    mock_llm = AsyncMock()
    mock_llm.get_text.side_effect = Exception("LLM Error")
    monkeypatch.setattr(dtools, "llm_service", mock_llm)

    res = await dtools.web_search.ainvoke("q")
    assert res == ""

@pytest.mark.asyncio
async def test_web_search_returns_non_string(mock_settings, mock_llm_tools_config, monkeypatch):
    mock_settings(allow_web=True)
    mock_llm_tools_config()
    
    mock_llm = AsyncMock(return_value=None)
    monkeypatch.setattr(dtools, "llm_service", mock_llm)

    res = await dtools.web_search.ainvoke("q")
    assert res == ""