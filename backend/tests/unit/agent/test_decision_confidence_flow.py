# backend/tests/unit/agent/test_decision_confidence_flow.py
"""UX-2026-07-02 — the decision loop must surface a confidence reading on
EVERY adaptive iteration.

Owner blackbox failure (twice): the FE closeness cue "kept saying the same
thing". Half of that bug lived here: two decision-path holes silently dropped
the freshest confidence, so ``current_confidence`` reached /quiz/status only
sporadically and the cue never escalated.

Hole 1 — the "FINISH_NOW but no resolvable winner" branch returned
``should_finalize=False`` WITHOUT ``current_confidence``, discarding the tool
call's freshest reading for that iteration.

Hole 2 — a decision-tool failure reset the surfaced confidence to 0.0,
regressing the cue to "no signal" even when a perfectly good reading had been
carried in state from the previous loop.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

import app.agent.graph as graph_mod
from app.agent.state import CharacterProfile, Synopsis

pytestmark = pytest.mark.unit


def _quiz_settings(monkeypatch, **overrides):
    quiz = SimpleNamespace(
        max_total_questions=20,
        min_questions_before_early_finish=3,
        depth_floor_min=3,
        early_finish_confidence=0.9,
        **overrides,
    )
    monkeypatch.setattr(graph_mod.settings, "quiz", quiz, raising=False)


# ---------------------------------------------------------------------------
# Hole 1 — no-winner ask-one-more still carries the fresh confidence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_winner_branch_carries_current_confidence(monkeypatch):
    """FINISH_NOW + unresolvable winner (below the cap) must ask one more AND
    write the iteration's confidence into state — not drop it on the floor."""

    async def stub_decision(*_a, **_k):
        return "FINISH_NOW", 0.8, "MissingName"

    monkeypatch.setattr(
        graph_mod, "_determine_decision_action", stub_decision, raising=True
    )

    state = {
        "session_id": uuid.uuid4(),
        "trace_id": "t",
        "synopsis": Synopsis(title="Quiz: Cats", summary=""),
        "generated_characters": [],  # nobody can win -> unresolvable
        "quiz_history": [{}] * 3,
        "baseline_count": 3,
        "topic_analysis": {},
    }
    out = await graph_mod._decide_or_finish_node(state)
    assert out["should_finalize"] is False
    assert out["current_confidence"] == 0.8


@pytest.mark.asyncio
async def test_unmatched_name_branch_carries_current_confidence(monkeypatch):
    """Same hole via the hallucinated-name path (candidates exist but none
    match): still asks one more, still surfaces the fresh confidence."""

    async def stub_decision(*_a, **_k):
        return "FINISH_NOW", 0.95, "Stranger"  # not in candidates

    monkeypatch.setattr(
        graph_mod, "_determine_decision_action", stub_decision, raising=True
    )

    state = {
        "session_id": uuid.uuid4(),
        "trace_id": "t",
        "synopsis": Synopsis(title="Quiz: Cats", summary=""),
        "generated_characters": [
            CharacterProfile(name="Alpha", short_description="", profile_text=""),
            CharacterProfile(name="Bravo", short_description="", profile_text=""),
        ],
        "quiz_history": [{}] * 5,
        "baseline_count": 3,
        "topic_analysis": {},
    }
    out = await graph_mod._decide_or_finish_node(state)
    assert out["should_finalize"] is False
    assert "final_result" not in out
    assert out["current_confidence"] == 0.95


# ---------------------------------------------------------------------------
# Hole 2 — decision-tool failure must not regress a carried confidence to 0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_failure_keeps_carried_confidence(monkeypatch):
    async def boom(*_a, **_k):
        raise RuntimeError("decide fail")

    monkeypatch.setattr(
        graph_mod,
        "tool_decide_next_step",
        SimpleNamespace(ainvoke=boom),
        raising=True,
    )
    _quiz_settings(monkeypatch)

    action, conf, name = await graph_mod._determine_decision_action(
        history_payload=[{}] * 10,
        characters_payload=[],
        synopsis_payload={},
        analysis={},
        trace_id="t",
        session_id="s",
        answered=10,
        current_confidence=0.55,  # carried in state from the previous loop
    )

    assert action == "ASK_ONE_MORE_QUESTION"
    assert conf == 0.55  # NOT regressed to 0.0
    assert name == ""


@pytest.mark.asyncio
async def test_tool_success_still_reports_fresh_reading(monkeypatch):
    """A SUCCESSFUL tool call overwrites the carried value — even downward.
    (Honesty: the cue may legitimately cool off; only a FAILURE holds the
    previous reading.)"""

    class StubTool:
        async def ainvoke(self, *_a, **_k):
            return SimpleNamespace(
                action="ASK_ONE_MORE_QUESTION",
                confidence=0.4,
                winning_character_name="",
            )

    monkeypatch.setattr(graph_mod, "tool_decide_next_step", StubTool(), raising=True)
    _quiz_settings(monkeypatch)

    _action, conf, _name = await graph_mod._determine_decision_action(
        history_payload=[{}] * 10,
        characters_payload=[],
        synopsis_payload={},
        analysis={},
        trace_id="t",
        session_id="s",
        answered=10,
        current_confidence=0.7,
    )
    assert conf == 0.4
