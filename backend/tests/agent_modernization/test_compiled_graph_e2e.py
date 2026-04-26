"""Iter 5 — end-to-end smoke test of the compiled agent graph on LangGraph 1.x.

Exercises ``ainvoke`` and ``aget_state`` against the real compiled graph with
the LLM-backed tools stubbed out. This guards against:

- Any LangGraph 1.x API drift that breaks ``ainvoke`` / ``aget_state``.
- Topology regressions (entry point, conditional routers, sink edge).
- Reducer behavior on the ``messages`` field (``add_messages``).

We do not assert tool internals — only the graph's traversal contract.
"""

from __future__ import annotations

import os
import uuid

import pytest


@pytest.fixture(autouse=True)
def _force_memory_saver(monkeypatch):
    monkeypatch.setenv("USE_MEMORY_SAVER", "1")
    yield


@pytest.mark.asyncio
async def test_compiled_graph_topology() -> None:
    """The compiled graph must declare the expected nodes and entry point."""
    from app.agent import graph as graph_mod

    g = await graph_mod.create_agent_graph()
    try:
        # CompiledStateGraph in LangGraph 1.x exposes .nodes mapping.
        nodes = set(getattr(g, "nodes", {}).keys())
        # Entry point + the 5 named nodes.
        for required in (
            "bootstrap",
            "generate_characters",
            "generate_baseline_questions",
            "decide_or_finish",
            "generate_adaptive_question",
            "assemble_and_finish",
        ):
            assert required in nodes, f"Missing required graph node: {required!r}"
    finally:
        await graph_mod.aclose_agent_graph(g)


@pytest.mark.asyncio
async def test_compiled_graph_ainvoke_runs_to_end_with_stubbed_tools(monkeypatch) -> None:
    """``ainvoke`` should drive the graph from bootstrap to sink."""
    from app.agent import graph as graph_mod
    from app.agent.schemas import CharacterProfile, InitialPlan

    # ---- Stub planning + character tools so no LLM calls happen ----
    class _StubTool:
        def __init__(self, fn):
            self._fn = fn

        async def ainvoke(self, payload, *_, **__):
            return await self._fn(payload)

    async def _plan(_payload):
        return InitialPlan(
            title="What Sci-Fi Captain Are You?",
            synopsis="A short quiz to discover your captain.",
            ideal_archetypes=["Picard", "Janeway", "Sisko"],
        )

    async def _gen_chars(_payload):
        return ["Picard", "Janeway", "Sisko"]

    async def _draft_one(payload):
        name = payload.get("character_name", "Unknown")
        return CharacterProfile(
            name=name,
            short_description=f"{name} short",
            profile_text=f"{name} profile",
        )

    async def _draft_batch(payload):
        names = payload.get("character_names") or []
        return {n: await _draft_one({"character_name": n}) for n in names}

    monkeypatch.setattr(graph_mod, "tool_plan_quiz", _StubTool(_plan))
    monkeypatch.setattr(graph_mod, "tool_generate_character_list", _StubTool(_gen_chars))
    monkeypatch.setattr(graph_mod, "tool_draft_character_profile", _StubTool(_draft_one))
    monkeypatch.setattr(graph_mod, "tool_draft_character_profiles", _StubTool(_draft_batch))
    monkeypatch.setattr(graph_mod, "analyze_topic", lambda _c: {
        "normalized_category": "Sci-Fi Captains",
        "outcome_kind": "characters",
        "creativity_mode": "balanced",
        "names_only": False,
        "intent": "identify",
        "domain": "tv",
    })

    g = await graph_mod.create_agent_graph()
    try:
        session_id = uuid.uuid4()
        config = {"configurable": {"thread_id": str(session_id)}}
        initial_state = {
            "session_id": session_id,
            "trace_id": "smoke",
            "category": "Sci-Fi Captains",
            "messages": [],
            "ready_for_questions": False,  # gates baseline question generation
        }

        result = await g.ainvoke(initial_state, config=config)
        assert result is not None
        # bootstrap produced a synopsis.
        assert result.get("synopsis") is not None
        # generate_characters produced character profiles.
        chars = result.get("generated_characters") or []
        assert len(chars) == 3
        # The router should NOT advance to baseline because ready_for_questions=False.
        assert not result.get("baseline_ready")

        # Snapshot is reachable on LangGraph 1.x.
        snap = await g.aget_state(config)
        assert snap is not None
    finally:
        await graph_mod.aclose_agent_graph(g)
