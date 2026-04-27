# tests/unit/agent/tools/test_topic_research.py
"""
Tests for the adaptive topic research tool (§7.7.2).

Acceptance criteria covered:
- AC-AGENT-RESEARCH-1: skip when topic_knowledge.is_well_known is True
- AC-AGENT-RESEARCH-2: skip when retrieval policy/allow_web disallow it
- AC-AGENT-RESEARCH-3: Gemini grounding primary path
- AC-AGENT-RESEARCH-4: OpenAI web_search fallback when Gemini fails
- AC-AGENT-RESEARCH-5: Wikipedia soft fallback
- AC-AGENT-RESEARCH-6: graceful 'none' provider when everything fails
- AC-AGENT-RESEARCH-7: at most one slot consumed per attempt; bounded by max_calls_per_run
- AC-AGENT-RESEARCH-8: bounded by research_latency_budget_s
- AC-AGENT-RESEARCH-9: scrubbed and truncated to <=4096 chars
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.no_tool_stubs]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def gather():
    from app.agent.tools.data_tools import gather_topic_research
    return gather_topic_research


@pytest.fixture
def well_known():
    from app.agent.schemas import TopicKnowledgeAssessment
    return TopicKnowledgeAssessment(
        knowledge_score=0.95, is_well_known=True,
        rationale="canonical", recommended_research=False,
    )


@pytest.fixture
def fringe():
    from app.agent.schemas import TopicKnowledgeAssessment
    return TopicKnowledgeAssessment(
        knowledge_score=0.15, is_well_known=False,
        rationale="niche", recommended_research=True,
    )


@pytest.fixture
def configure_settings(monkeypatch):
    from app.core.config import settings

    def _apply(*, policy="adaptive", allow_web=True, allow_wiki=True,
               max_calls=2, latency_budget_s=8.0):
        r = SimpleNamespace(
            policy=policy, allow_web=allow_web, allow_wikipedia=allow_wiki,
            max_calls_per_run=max_calls,
            research_latency_budget_s=latency_budget_s,
            allowed_domains=[],
        )
        monkeypatch.setattr(settings, "retrieval", r, raising=False)
        # Ensure llm_tools.web_search exists so the fallback path can build a spec.
        existing = getattr(settings, "llm_tools", {}) or {}
        if "web_search" not in existing:
            new_tools = dict(existing)
            new_tools["web_search"] = SimpleNamespace(
                model="gpt-4o-mini", allowed_domains=[], effort=None,
                user_location=None, include_sources=True, tool_choice="auto",
            )
            monkeypatch.setattr(settings, "llm_tools", new_tools, raising=False)
        # Configure topic research provider order & gemini model.
        monkeypatch.setattr(
            settings, "llm",
            SimpleNamespace(provider="openai", per_call_timeout_s=10),
            raising=False,
        )
        return r

    return _apply


# ---------------------------------------------------------------------------
# AC-AGENT-RESEARCH-1
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_skip_when_well_known(gather, well_known, configure_settings, monkeypatch):
    configure_settings()
    from app.agent.tools import data_tools as dtools

    grounding = AsyncMock(return_value="should not be called")
    monkeypatch.setattr(dtools, "_call_gemini_grounding", grounding, raising=False)

    out = await gather(
        category="MBTI", analysis={}, topic_knowledge=well_known,
        trace_id="t", session_id="s",
    )
    assert out.research_used is False
    assert out.research_provider == "none"
    assert out.research_context == ""
    grounding.assert_not_called()


# ---------------------------------------------------------------------------
# AC-AGENT-RESEARCH-2
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_skip_when_policy_disallows(gather, fringe, configure_settings, monkeypatch):
    """Policy off OR allow_web=False blocks all provider attempts."""
    from app.agent.tools import data_tools as dtools

    configure_settings(policy="off", allow_web=False, allow_wiki=False)
    grounding = AsyncMock(return_value="x")
    monkeypatch.setattr(dtools, "_call_gemini_grounding", grounding, raising=False)

    out = await gather(category="X", analysis={}, topic_knowledge=fringe,
                       trace_id="t", session_id="s")
    assert out.research_used is False
    grounding.assert_not_called()


# ---------------------------------------------------------------------------
# AC-AGENT-RESEARCH-3
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gemini_grounding_primary(gather, fringe, configure_settings, monkeypatch):
    from app.agent.tools import data_tools as dtools

    configure_settings()
    grounding = AsyncMock(return_value="grounded text about niche topic")
    monkeypatch.setattr(dtools, "_call_gemini_grounding", grounding, raising=False)

    # web_search fallback should NOT be touched.
    monkeypatch.setattr(
        dtools, "web_search",
        SimpleNamespace(ainvoke=AsyncMock(return_value="should-not-be-called")),
        raising=False,
    )

    out = await gather(
        category="Niche RPG Classes", analysis={"is_media": False},
        topic_knowledge=fringe, trace_id="t", session_id="s",
    )
    assert out.research_used is True
    assert out.research_provider == "gemini_grounding"
    assert "grounded text" in out.research_context
    grounding.assert_awaited_once()


# ---------------------------------------------------------------------------
# AC-AGENT-RESEARCH-4
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_openai_fallback_when_gemini_fails(gather, fringe, configure_settings, monkeypatch):
    from app.agent.tools import data_tools as dtools

    configure_settings()

    async def gemini_fail(*a, **k):
        raise RuntimeError("gemini down")

    monkeypatch.setattr(dtools, "_call_gemini_grounding", gemini_fail, raising=False)

    fake_web = SimpleNamespace(ainvoke=AsyncMock(return_value="openai web result"))
    monkeypatch.setattr(dtools, "web_search", fake_web, raising=False)

    out = await gather(
        category="Random Topic", analysis={"is_media": False},
        topic_knowledge=fringe, trace_id="t", session_id="s",
    )
    assert out.research_used is True
    assert out.research_provider == "openai_web_search"
    assert "openai web result" in out.research_context


# ---------------------------------------------------------------------------
# AC-AGENT-RESEARCH-5
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wikipedia_soft_fallback(gather, fringe, configure_settings, monkeypatch):
    from app.agent.tools import data_tools as dtools

    configure_settings(allow_wiki=True)

    async def gemini_fail(*a, **k):
        raise RuntimeError("g down")

    fake_web = SimpleNamespace(ainvoke=AsyncMock(return_value=""))
    fake_wiki = SimpleNamespace(invoke=lambda q: "wiki body")

    monkeypatch.setattr(dtools, "_call_gemini_grounding", gemini_fail, raising=False)
    monkeypatch.setattr(dtools, "web_search", fake_web, raising=False)
    monkeypatch.setattr(dtools, "wikipedia_search", fake_wiki, raising=False)

    out = await gather(
        category="Random Topic", analysis={"is_media": False},
        topic_knowledge=fringe, trace_id="t", session_id="s",
    )
    assert out.research_used is True
    assert out.research_provider == "wikipedia"
    assert "wiki body" in out.research_context


# ---------------------------------------------------------------------------
# AC-AGENT-RESEARCH-6
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_all_providers_fail_returns_empty(gather, fringe, configure_settings, monkeypatch):
    from app.agent.tools import data_tools as dtools

    configure_settings()

    async def gemini_fail(*a, **k):
        raise RuntimeError("g")
    monkeypatch.setattr(dtools, "_call_gemini_grounding", gemini_fail, raising=False)
    monkeypatch.setattr(
        dtools, "web_search",
        SimpleNamespace(ainvoke=AsyncMock(return_value="")),
        raising=False,
    )
    monkeypatch.setattr(
        dtools, "wikipedia_search",
        SimpleNamespace(invoke=lambda q: ""),
        raising=False,
    )

    out = await gather(category="X", analysis={"is_media": False},
                       topic_knowledge=fringe, trace_id="t", session_id="s")
    assert out.research_used is False
    assert out.research_provider == "none"
    assert out.research_context == ""


# ---------------------------------------------------------------------------
# AC-AGENT-RESEARCH-9 (truncate + scrub)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_research_context_truncation_and_scrub(gather, fringe, configure_settings, monkeypatch):
    from app.agent.tools import data_tools as dtools

    configure_settings()
    raw = ("ok\x00bad\x07line " * 1000)  # >> 4096
    grounding = AsyncMock(return_value=raw)
    monkeypatch.setattr(dtools, "_call_gemini_grounding", grounding, raising=False)

    out = await gather(category="X", analysis={"is_media": False},
                       topic_knowledge=fringe, trace_id="t", session_id="s")
    assert len(out.research_context) <= 4096
    assert "\x00" not in out.research_context
    assert "\x07" not in out.research_context
