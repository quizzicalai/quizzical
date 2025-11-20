# tests/unit/agent/tools/test_planning_tools.py

import pytest
import types
from unittest.mock import MagicMock
from typing import get_origin
from pydantic import ValidationError

from app.agent.tools import planning_tools
from app.agent.schemas import (
    InitialPlan,
    CharacterArchetypeList,
    CharacterCastingDecision,
    NormalizedTopic,
)

# Import the REAL implementations to restore them for this module
from app.agent.tools.planning_tools import (
    plan_quiz as _real_plan_quiz,
    generate_character_list as _real_generate_character_list,
    select_characters_for_reuse as _real_select_characters_for_reuse,
)

pytestmark = pytest.mark.unit


# -----------------------------
# Fixtures
# -----------------------------

@pytest.fixture(autouse=True)
def _restore_real_planning_tools(monkeypatch):
    """
    Bypass the global 'stub_all_tools' fixture for this test module.
    We want to test the actual logic of planning tools.
    """
    monkeypatch.setattr(planning_tools, "plan_quiz", _real_plan_quiz, raising=False)
    monkeypatch.setattr(planning_tools, "generate_character_list", _real_generate_character_list, raising=False)
    monkeypatch.setattr(planning_tools, "select_characters_for_reuse", _real_select_characters_for_reuse, raising=False)


@pytest.fixture(autouse=True)
def _enable_retrieval_policy(monkeypatch):
    """Ensure retrieval is allowed so we can test paths that use it."""
    # Mock global settings.retrieval
    monkeypatch.setattr(
        planning_tools.settings,
        "retrieval",
        types.SimpleNamespace(
            policy="all",
            allow_wikipedia=True,
            allow_web=True,
            max_calls_per_run=10,
            allowed_domains=[],
        ),
        raising=False,
    )
    
    # Also clear budget in data_tools to ensure clean state
    from app.agent.tools import data_tools as dtools
    monkeypatch.setattr(dtools.settings, "retrieval", planning_tools.settings.retrieval, raising=False)
    monkeypatch.setattr(dtools, "_RETRIEVAL_BUDGET", {}, raising=False)


# -----------------------------
# Small helpers / stubs
# -----------------------------
class _StubTool:
    """Mimics a LangChain Tool with .ainvoke returning a preset value."""
    def __init__(self, value, *, on_call=None):
        self._value = value
        self._on_call = on_call

    async def ainvoke(self, _args, **kwargs):
        if self._on_call:
            self._on_call(_args)
        return self._value


def _fake_gsr_factory(func):
    """Helper to create a fake get_structured_response implementation."""
    async def wrapper(*args, **kwargs):
        if callable(func):
            return await func(**kwargs)
        return func
    return wrapper


# =============================
# plan_quiz
# =============================

@pytest.mark.asyncio
async def test_plan_quiz_happy_path(llm_spy, monkeypatch):
    """Verify plan_quiz calls initial_planner and returns InitialPlan."""
    
    async def _success(**kwargs):
        return InitialPlan(
            title="Quiz: The Expanse",
            synopsis="A fun, fast quiz exploring The Expanse characters.",
            ideal_archetypes=["The Pilot", "The Belter", "The Politico", "The Detective"],
            ideal_count_hint=4
        )

    monkeypatch.setattr(planning_tools.llm_service, "get_structured_response", _fake_gsr_factory(_success), raising=True)

    plan = await planning_tools.plan_quiz.ainvoke({"category": "The Expanse"})
    
    assert isinstance(plan, InitialPlan)
    assert plan.synopsis.startswith("A fun, fast quiz")
    assert len(plan.ideal_archetypes) == 4
    
    # Verify LLM call args
    assert llm_spy["tool_name"] == "initial_planner"
    assert llm_spy["response_model"] == InitialPlan


@pytest.mark.asyncio
async def test_plan_quiz_fallback_on_error(monkeypatch):
    """If LLM fails, return a safe fallback plan."""
    async def _boom(**_):
        raise RuntimeError("LLM exploded")

    monkeypatch.setattr(planning_tools.llm_service, "get_structured_response", _fake_gsr_factory(_boom), raising=True)

    plan = await planning_tools.plan_quiz.ainvoke({"category": "Dogs"})
    
    # The fallback logic uses the normalized category title
    # Since we didn't mock analyze_topic, it runs locally and likely returns "Type of Dog" or "Dogs"
    assert "Quiz" in (plan.title or "") or "What" in (plan.title or "")
    assert plan.ideal_archetypes == []
    # Fallback plan doesn't have a synopsis other than default
    assert "fun quiz" in plan.synopsis


@pytest.mark.asyncio
async def test_plan_quiz_uses_canonical_sets(monkeypatch):
    """If canonical set exists, plan should use it."""
    # Mock canonical_for to return a fixed list
    monkeypatch.setattr(planning_tools, "canonical_for", lambda cat: ["A", "B", "C"], raising=True)
    monkeypatch.setattr(planning_tools, "count_hint_for", lambda cat: 3, raising=True)

    # Even if LLM returns something else, the tool should override it
    async def _llm_plan(**kwargs):
        return InitialPlan(
            synopsis="LLM synopsis",
            ideal_archetypes=["X", "Y"] # Should be ignored/overridden
        )
    
    monkeypatch.setattr(planning_tools.llm_service, "get_structured_response", _fake_gsr_factory(_llm_plan), raising=True)

    plan = await planning_tools.plan_quiz.ainvoke({"category": "FixedSet"})
    
    assert plan.ideal_archetypes == ["A", "B", "C"]
    assert plan.ideal_count_hint == 3


# =============================
# generate_character_list
# =============================

@pytest.mark.asyncio
async def test_generate_character_list_media_prefers_wiki_then_web(monkeypatch):
    """
    Test retrieval logic: 
    1. Detects media topic
    2. Tries Wiki (we verify it's called)
    3. If Wiki fails, tries Web (we verify it's called)
    """
    from app.agent.tools import data_tools as data_tools_mod

    # Force analyze_topic to return is_media=True
    monkeypatch.setattr(planning_tools, "analyze_topic", lambda c: {
        "normalized_category": c, "is_media": True, "creativity_mode": "balanced", "outcome_kind": "characters"
    })

    wiki_called = {"count": 0}
    web_called = {"count": 0}

    # Stub Wiki to return empty string (failure/no result)
    async def _wiki_hook(payload):
        wiki_called["count"] += 1
        return "" 

    # Stub Web to return success
    async def _web_hook(payload, **_):
        web_called["count"] += 1
        return "Search results found characters: Lorelai, Rory."

    # We need to patch the actual tool instances in the data_tools module
    monkeypatch.setattr(data_tools_mod.wikipedia_search, "ainvoke", _wiki_hook, raising=False)
    monkeypatch.setattr(data_tools_mod.web_search, "ainvoke", _web_hook, raising=False)

    # Stub LLM
    async def _llm_gen(**kwargs):
        # The tool now returns List[str] directly due to logic extraction
        # But wait, the tool actually calls invoke_structured with CharacterArchetypeList
        return CharacterArchetypeList(archetypes=["Lorelai", "Rory"])

    monkeypatch.setattr(planning_tools.llm_service, "get_structured_response", _fake_gsr_factory(_llm_gen), raising=True)

    labels = await planning_tools.generate_character_list.ainvoke({
        "category": "Gilmore Girls",
        "synopsis": "A cozy small-town character quiz.",
    })

    assert wiki_called["count"] == 1
    assert web_called["count"] == 1
    assert labels == ["Lorelai", "Rory"]


@pytest.mark.asyncio
async def test_generate_character_list_fallback_parsing(monkeypatch):
    """
    If primary schema parsing fails (CharacterArchetypeList), 
    tool should retry with a raw list TypeAdapter.
    """
    calls = {"n": 0}

    async def _flaky_llm(**kwargs):
        calls["n"] += 1
        response_model = kwargs.get("response_model")
        
        # First call: Requesting CharacterArchetypeList -> Fail
        if response_model is CharacterArchetypeList:
            raise ValidationError.from_exception_data("fail", [{"type": "value_error", "loc": (), "input": {}, "msg": "error"}])
        
        # Second call: Requesting List[str] -> Success
        return ["Analyst", "Dreamer"]

    monkeypatch.setattr(planning_tools.llm_service, "get_structured_response", _fake_gsr_factory(_flaky_llm), raising=True)

    # Prevent retrieval to keep test focused
    monkeypatch.setattr(planning_tools, "analyze_topic", lambda c: {
        "normalized_category": c, "is_media": False, "creativity_mode": "whimsical", "outcome_kind": "types"
    })

    labels = await planning_tools.generate_character_list.ainvoke({
        "category": "General Archetypes",
        "synopsis": "A creative archetype quiz.",
    })
    
    assert labels == ["Analyst", "Dreamer"]
    assert calls["n"] == 2  # Primary + Fallback


@pytest.mark.asyncio
async def test_generate_character_list_canonical_short_circuit(monkeypatch):
    """If canonical list exists, return it immediately without LLM."""
    monkeypatch.setattr(planning_tools, "canonical_for", lambda cat: ["Alpha", "Beta"], raising=True)
    
    # Ensure LLM would explode if called
    async def _boom(**_): raise RuntimeError("Should not be called")
    monkeypatch.setattr(planning_tools.llm_service, "get_structured_response", _fake_gsr_factory(_boom), raising=True)

    labels = await planning_tools.generate_character_list.ainvoke({
        "category": "Greek Letters",
        "synopsis": "...",
    })

    assert labels == ["Alpha", "Beta"]


# =============================
# select_characters_for_reuse
# =============================

@pytest.mark.asyncio
async def test_select_characters_for_reuse_happy(llm_spy, monkeypatch):
    ideal = ["The Optimist", "The Analyst"]
    retrieved = [{"name": "The Optimist", "profile_text": "..."}]

    async def _success(**kwargs):
        return CharacterCastingDecision(
            reuse=[{"ideal_name": "The Optimist", "existing_name": "The Optimist", "reason": "Match"}],
            improve=[],
            create=["The Analyst"]
        )

    monkeypatch.setattr(planning_tools.llm_service, "get_structured_response", _fake_gsr_factory(_success), raising=True)

    decision = await planning_tools.select_characters_for_reuse.ainvoke({
        "category": "Cats",
        "ideal_archetypes": ideal,
        "retrieved_characters": retrieved,
    })

    assert isinstance(decision, CharacterCastingDecision)
    assert len(decision.reuse) == 1
    assert decision.reuse[0].ideal_name == "The Optimist"
    assert decision.create == ["The Analyst"]
    
    assert llm_spy["tool_name"] == "character_selector"