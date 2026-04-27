# tests/unit/agent/tools/test_topic_knowledge_classifier.py
"""
Tests for the topic knowledge classifier (§7.7.1).

Acceptance criteria covered:
- AC-AGENT-KNOW-1: canonical short-circuit (no LLM call)
- AC-AGENT-KNOW-2: factual + framework/profession domain short-circuit (no LLM call)
- AC-AGENT-KNOW-3: LLM call for unknown topics; fail-open on exception
- AC-AGENT-KNOW-4: bootstrap node sets state["topic_knowledge"] before plan_quiz
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.no_tool_stubs]


@pytest.fixture
def classify():
    from app.agent.tools.planning_tools import classify_topic_knowledge
    return classify_topic_knowledge


@pytest.fixture
def assessment_model():
    from app.agent.schemas import TopicKnowledgeAssessment
    return TopicKnowledgeAssessment


# ---------------------------------------------------------------------------
# AC-AGENT-KNOW-1: canonical short-circuit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_canonical_topic_short_circuits_without_llm(classify, monkeypatch):
    """If canonical_for(...) returns a non-empty list, no LLM call is made."""
    from app.agent.tools import planning_tools as ptools

    monkeypatch.setattr(ptools, "canonical_for", lambda c: ["INTJ", "ENFP", "ISTP"])

    async def boom(*a, **k):
        raise AssertionError("LLM must not be called for canonical topics")

    monkeypatch.setattr(ptools, "invoke_structured", boom)

    out = await classify(
        category="MBTI",
        analysis={"normalized_category": "MBTI", "creativity_mode": "factual",
                  "domain": "frameworks_types_systems", "is_media": False},
    )
    assert out.is_well_known is True
    assert out.knowledge_score == 1.0
    assert out.recommended_research is False


# ---------------------------------------------------------------------------
# AC-AGENT-KNOW-2: factual + serious-domain short-circuit
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_factual_serious_domain_short_circuits_without_llm(classify, monkeypatch):
    from app.agent.tools import planning_tools as ptools

    monkeypatch.setattr(ptools, "canonical_for", lambda c: None)

    async def boom(*a, **k):
        raise AssertionError("LLM must not be called for factual+framework topics")

    monkeypatch.setattr(ptools, "invoke_structured", boom)

    for domain in ("frameworks_types_systems", "serious_professions_profiles"):
        out = await classify(
            category="DISC Personality Types",
            analysis={"normalized_category": "DISC", "creativity_mode": "factual",
                      "domain": domain, "is_media": False},
        )
        assert out.is_well_known is True
        assert out.recommended_research is False


# ---------------------------------------------------------------------------
# AC-AGENT-KNOW-3: LLM call for unknown topics, fail-open on exception
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unknown_topic_invokes_llm_and_uses_result(classify, assessment_model, monkeypatch):
    from app.agent.tools import planning_tools as ptools

    monkeypatch.setattr(ptools, "canonical_for", lambda c: None)

    captured = {}

    async def fake_invoke(**kwargs):
        captured.update(kwargs)
        return assessment_model(
            knowledge_score=0.2,
            is_well_known=False,
            rationale="Niche tabletop RPG",
            recommended_research=True,
        )

    monkeypatch.setattr(ptools, "invoke_structured", fake_invoke)

    out = await classify(
        category="Niche Tabletop RPG Classes",
        analysis={"normalized_category": "Niche Tabletop RPG Classes",
                  "creativity_mode": "balanced", "domain": "", "is_media": False},
    )
    assert captured.get("tool_name") == "topic_knowledge_classifier"
    assert out.is_well_known is False
    assert 0.0 <= out.knowledge_score <= 1.0
    assert out.recommended_research is True


@pytest.mark.asyncio
async def test_classifier_fails_open_on_llm_exception(classify, monkeypatch):
    from app.agent.tools import planning_tools as ptools

    monkeypatch.setattr(ptools, "canonical_for", lambda c: None)

    async def boom(**_):
        raise RuntimeError("LLM down")

    monkeypatch.setattr(ptools, "invoke_structured", boom)

    out = await classify(
        category="Some Unknown Thing",
        analysis={"normalized_category": "Some Unknown Thing",
                  "creativity_mode": "balanced", "domain": "", "is_media": False},
    )
    # Fail-open: skip research, treat as well-known.
    assert out.is_well_known is True
    assert out.knowledge_score == 1.0
    assert out.recommended_research is False


# ---------------------------------------------------------------------------
# AC-AGENT-KNOW-4: bootstrap stores topic_knowledge in state
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bootstrap_node_stores_topic_knowledge(monkeypatch, assessment_model):
    """After bootstrap runs, GraphState['topic_knowledge'] must be populated."""
    from app.agent import graph as graph_mod
    from app.agent.schemas import InitialPlan

    # Stub plan_quiz tool so bootstrap completes.
    class _StubTool:
        def __init__(self, fn):
            self._fn = fn

        async def ainvoke(self, payload, *_, **__):
            return await self._fn(payload)

    async def _plan(_p):
        return InitialPlan(title="Quiz: Cats", synopsis="A fun quiz.",
                           ideal_archetypes=["A", "B", "C", "D"])

    async def _gen_chars(_p):
        return ["A", "B", "C", "D"]

    monkeypatch.setattr(graph_mod, "tool_plan_quiz", _StubTool(_plan))
    monkeypatch.setattr(graph_mod, "tool_generate_character_list", _StubTool(_gen_chars))
    monkeypatch.setattr(graph_mod, "analyze_topic", lambda _c: {
        "normalized_category": "Cats", "outcome_kind": "characters",
        "creativity_mode": "balanced", "names_only": False,
        "intent": "identify", "domain": "animals_species_breeds", "is_media": False,
    })

    # Stub the classifier in graph_mod's namespace.
    async def _fake_classifier(category, analysis):
        return assessment_model(
            knowledge_score=0.9, is_well_known=True,
            rationale="Common topic.", recommended_research=False,
        )

    monkeypatch.setattr(graph_mod, "classify_topic_knowledge", _fake_classifier, raising=False)

    state = {
        "session_id": __import__("uuid").uuid4(),
        "trace_id": "test",
        "category": "Cats",
        "messages": [],
    }
    out = await graph_mod._bootstrap_node(state)
    tk = out.get("topic_knowledge")
    assert tk is not None
    assert tk.is_well_known is True
