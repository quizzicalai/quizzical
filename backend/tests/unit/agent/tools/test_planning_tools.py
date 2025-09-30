# backend/tests/unit/test_planning_tools.py

import pytest
from types import SimpleNamespace
from typing import get_origin
from pydantic import ValidationError

from app.agent.tools import planning_tools


# -----------------------------
# Small helpers / stubs
# -----------------------------
class _StubTool:
    """Mimics a LangChain Tool with .ainvoke returning a preset value."""
    def __init__(self, value, *, on_call=None):
        self._value = value
        self._on_call = on_call

    async def ainvoke(self, _args):
        if self._on_call:
            self._on_call(_args)
        return self._value


def _make_validation_error():
    return ValidationError.from_exception_data(
        "List[str]",
        [
            {
                "type": "value_error",
                "loc": ("root",),
                "msg": "stubbed structured-output mismatch",
                "input": None,
                "ctx": {"error": ValueError("stubbed structured-output mismatch")},
            }
        ],
    )


# =============================
# normalize_topic
# =============================

@pytest.mark.asyncio
async def test_normalize_topic_llm_path_uses_research(monkeypatch):
    # Track that web_search was invoked
    called = {"web": False}

    # Stub: web_search returns some disambiguation context
    from app.agent.tools import data_tools as data_tools_mod
    monkeypatch.setattr(
        data_tools_mod,
        "web_search",
        _StubTool("Gilmore Girls is an American TV series.", on_call=lambda _: called.__setitem__("web", True)),
        raising=True,
    )

    # Stub LLM: return a fully formed NormalizedTopic
    async def fake_structured(tool_name, messages, response_model, trace_id=None, session_id=None):
        assert tool_name == "topic_normalizer"
        assert response_model is planning_tools.NormalizedTopic
        return planning_tools.NormalizedTopic(
            category="Gilmore Girls Characters",
            outcome_kind="characters",
            creativity_mode="balanced",
            rationale="TV series; produce character outcomes.",
        )

    monkeypatch.setattr(planning_tools.llm_service, "get_structured_response", fake_structured, raising=True)

    out = await planning_tools.normalize_topic.ainvoke({"category": "Gilmore Girls"})
    assert isinstance(out, planning_tools.NormalizedTopic)
    assert out.category == "Gilmore Girls Characters"
    assert out.outcome_kind == "characters"
    assert out.creativity_mode == "balanced"
    assert called["web"] is True


@pytest.mark.asyncio
async def test_normalize_topic_fallback_heuristic_on_llm_error(monkeypatch):
    # Even if web search fails, we should still return heuristic result
    from app.agent.tools import data_tools as data_tools_mod
    monkeypatch.setattr(
        data_tools_mod, "web_search", _StubTool(value=""), raising=True
    )

    async def boom(*_a, **_k):
        raise RuntimeError("LLM blew up")

    monkeypatch.setattr(planning_tools.llm_service, "get_structured_response", boom, raising=True)

    out = await planning_tools.normalize_topic.ainvoke({"category": "Dogs"})
    # Heuristic path for short plural noun → "Type of <Singular>" + whimsical/types
    assert out.category == "Type of Dog"
    assert out.outcome_kind == "types"
    assert out.creativity_mode == "whimsical"
    assert "Heuristic" in out.rationale


# =============================
# plan_quiz
# =============================

@pytest.mark.asyncio
async def test_plan_quiz_happy(monkeypatch):
    # LLM returns a valid InitialPlan
    async def fake_structured(tool_name, messages, response_model, trace_id=None, session_id=None):
        assert tool_name == "initial_planner"
        assert response_model is planning_tools.InitialPlan
        return planning_tools.InitialPlan(
            synopsis="A fun, fast quiz exploring The Expanse characters.",
            ideal_archetypes=["The Pilot", "The Belter", "The Politico", "The Detective"],
        )

    monkeypatch.setattr(planning_tools.llm_service, "get_structured_response", fake_structured, raising=True)

    plan = await planning_tools.plan_quiz.ainvoke({"category": "The Expanse"})
    assert plan.synopsis.startswith("A fun, fast quiz")
    assert len(plan.ideal_archetypes) >= 4


@pytest.mark.asyncio
async def test_plan_quiz_fallback_on_error(monkeypatch):
    async def boom(*_a, **_k):
        raise RuntimeError("nope")

    monkeypatch.setattr(planning_tools.llm_service, "get_structured_response", boom, raising=True)

    plan = await planning_tools.plan_quiz.ainvoke({"category": "Dogs"})
    # Uses normalized category in fallback message ("Type of Dog")
    assert plan.synopsis == "A fun quiz about Type of Dog."
    assert plan.ideal_archetypes == []


# =============================
# generate_character_list
# =============================

@pytest.mark.asyncio
async def test_generate_character_list_media_prefers_wiki_then_web(monkeypatch):
    # Media topic → is_media True → try Wikipedia first, then web fallback
    from app.agent.tools import data_tools as data_tools_mod

    wiki_called = {"count": 0}
    web_called = {"count": 0}

    async def _wiki_hook(_args):
        wiki_called["count"] += 1
        return ""  # force fallback to web

    async def _web_hook(_args):
        web_called["count"] += 1
        return "List of main characters: Lorelai, Rory, Luke, Sookie"

    monkeypatch.setattr(data_tools_mod, "wikipedia_search", _StubTool(value="", on_call=lambda a: None), raising=True)
    # Overwrite with a custom stub that increments count and returns empty string
    data_tools_mod.wikipedia_search.ainvoke = _wiki_hook  # type: ignore[attr-defined]

    monkeypatch.setattr(data_tools_mod, "web_search", _StubTool(value="", on_call=lambda a: None), raising=True)
    data_tools_mod.web_search.ainvoke = _web_hook  # type: ignore[attr-defined]

    # LLM returns an array of names (with an empty that should be scrubbed)
    async def fake_structured(tool_name, messages, response_model, trace_id=None, session_id=None):
        assert tool_name == "character_list_generator"
        assert (get_origin(response_model) or response_model) is list
        return ["Lorelai", "Rory", "Luke", "Sookie", ""]

    monkeypatch.setattr(planning_tools.llm_service, "get_structured_response", fake_structured, raising=True)

    labels = await planning_tools.generate_character_list.ainvoke({
        "category": "Gilmore Girls",
        "synopsis": "A cozy small-town character quiz.",
    })

    assert wiki_called["count"] == 1
    assert web_called["count"] == 1
    assert labels == ["Lorelai", "Rory", "Luke", "Sookie"]


@pytest.mark.asyncio
async def test_generate_character_list_legacy_object_path(monkeypatch):
    # Force first call to raise ValidationError, then return object with .archetypes
    calls = {"n": 0}

    async def fake_structured(tool_name, messages, response_model, trace_id=None, session_id=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _make_validation_error()
        # Legacy path: return an object with .archetypes
        return SimpleNamespace(archetypes=["Analyst", "Dreamer", "Builder", "Sage"])

    # No research needed here; stub them harmlessly
    from app.agent.tools import data_tools as data_tools_mod
    monkeypatch.setattr(data_tools_mod, "wikipedia_search", _StubTool(""), raising=True)
    monkeypatch.setattr(data_tools_mod, "web_search", _StubTool(""), raising=True)

    monkeypatch.setattr(planning_tools.llm_service, "get_structured_response", fake_structured, raising=True)

    labels = await planning_tools.generate_character_list.ainvoke({
        "category": "General Archetypes",
        "synopsis": "A creative archetype quiz.",
    })
    assert labels == ["Analyst", "Dreamer", "Builder", "Sage"]
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_generate_character_list_creative_skips_research(monkeypatch):
    # For creative non-media types, tool should NOT call wiki/web
    called = {"wiki": False, "web": False}

    from app.agent.tools import data_tools as data_tools_mod

    async def _oops(_args):
        # If either research path is invoked, fail the test
        raise AssertionError("Research should not be called for creative/non-media topics")

    # Install stubs that would explode if called
    monkeypatch.setattr(data_tools_mod, "wikipedia_search", _StubTool("", on_call=lambda a: called.__setitem__("wiki", True)), raising=True)
    data_tools_mod.wikipedia_search.ainvoke = _oops  # type: ignore[attr-defined]
    monkeypatch.setattr(data_tools_mod, "web_search", _StubTool("", on_call=lambda a: called.__setitem__("web", True)), raising=True)
    data_tools_mod.web_search.ainvoke = _oops  # type: ignore[attr-defined]

    async def fake_structured(tool_name, messages, response_model, trace_id=None, session_id=None):
        assert tool_name == "character_list_generator"
        return ["Sweet Tooth", "Savory Fan", "Health Nut", "Brunch Boss"]

    monkeypatch.setattr(planning_tools.llm_service, "get_structured_response", fake_structured, raising=True)

    labels = await planning_tools.generate_character_list.ainvoke({
        "category": "Breakfasts",
        "synopsis": "Find your breakfast persona.",
    })
    assert labels and len(labels) == 4
    assert called["wiki"] is False and called["web"] is False


# =============================
# select_characters_for_reuse
# =============================

@pytest.mark.asyncio
async def test_select_characters_for_reuse_happy(monkeypatch):
    ideal = ["The Optimist", "The Analyst", "The Skeptic"]
    retrieved = [
        {"name": "The Optimist", "short_description": "Bright outlook", "profile_text": "..."},
        {"name": "The Analyst", "short_description": "Thinks deeply", "profile_text": "..."},
    ]

    async def fake_structured(tool_name, messages, response_model, trace_id=None, session_id=None):
        assert tool_name == "character_selector"
        assert response_model is planning_tools.CharacterCastingDecision
        return planning_tools.CharacterCastingDecision(
            reuse=[retrieved[0]],
            improve=[retrieved[1]],
            create=["The Adventurer"],
        )

    monkeypatch.setattr(planning_tools.llm_service, "get_structured_response", fake_structured, raising=True)

    decision = await planning_tools.select_characters_for_reuse.ainvoke({
        "category": "Cats",
        "ideal_archetypes": ideal,
        "retrieved_characters": retrieved,
    })

    assert isinstance(decision, planning_tools.CharacterCastingDecision)
    assert len(decision.reuse) == 1
    assert len(decision.improve) == 1
    assert decision.create == ["The Adventurer"]
