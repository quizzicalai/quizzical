# tests/unit/agent/test_graph_blended_routing.py

"""
Routing tests for the blended-profile PILOT inside `_decide_or_finish_node`.

These confirm the gate wires the right writer:
  - a pilot blended topic (DISC) -> write_blended_profile (blended path);
  - a non-pilot canonical-blended topic (Big Five) -> write_final_user_profile
    (UNCHANGED single-character path);
  - a single/non-canonical topic (Harry Potter) -> write_final_user_profile.

We stub `_determine_decision_action` so the node always reaches the writer with
a resolved winner, and replace both writer tools with spies so we can see which
one the gate selected (no LLM is called).
"""

from types import SimpleNamespace

import pytest

import app.agent.graph as graph_mod
from app.agent.state import CharacterProfile
from app.models.api import BlendedDimension, BlendedProfile, FinalResult


def _set_pilot_allowlist(monkeypatch, allowlist):
    proxy = graph_mod.settings
    ov = object.__getattribute__(proxy, "_overrides")
    ov.clear()
    proxy.quiz = SimpleNamespace(
        max_total_questions=20,
        min_questions_before_early_finish=5,
        early_finish_confidence=0.9,
        blended_outcome_pilot=allowlist,
    )


class _Spy:
    def __init__(self, result):
        self.result = result
        self.called = False
        self.kwargs = None

    async def ainvoke(self, payload):
        self.called = True
        self.kwargs = payload
        return self.result


def _wire_writers(monkeypatch):
    single = _Spy(FinalResult(title="Single", description="x" * 420))
    blended = _Spy(
        FinalResult(
            title="Blend",
            description="y" * 420,
            result_kind="blended_profile",
            profile=BlendedProfile(
                dimensions=[BlendedDimension(name="Dominance", emphasis=80, blurb="b")],
                primary="Dominance",
                narrative="y" * 420,
            ),
        )
    )
    monkeypatch.setattr(graph_mod, "tool_write_final_user_profile", single, raising=True)
    monkeypatch.setattr(graph_mod, "tool_write_blended_profile", blended, raising=True)
    return single, blended


async def _run_node(monkeypatch, category):
    async def fake_decide(*_args, **_kwargs):
        return ("FINISH_NOW", 0.95, "Hero")

    monkeypatch.setattr(graph_mod, "_determine_decision_action", fake_decide, raising=True)

    chars = [CharacterProfile(name="Hero", short_description="s", profile_text="p")]
    state = {
        "session_id": "s-1",
        "trace_id": "t-1",
        "synopsis": {"title": category, "summary": "sum"},
        "generated_characters": chars,
        "quiz_history": [{"q": "x", "a": "y"}] * 6,
        "baseline_count": 5,
        "category": category,
        "outcome_kind": "types",
        "creativity_mode": "balanced",
    }
    return await graph_mod._decide_or_finish_node(state)


@pytest.mark.asyncio
async def test_disc_routes_to_blended_writer(monkeypatch):
    _set_pilot_allowlist(monkeypatch, ["disc"])
    single, blended = _wire_writers(monkeypatch)

    out = await _run_node(monkeypatch, "What is my DISC type")

    assert blended.called is True
    assert single.called is False
    assert out["final_result"].result_kind == "blended_profile"
    # The canonical DISC palette is passed to the blended writer.
    assert blended.kwargs["dimensions"] == [
        "Dominance",
        "Influence",
        "Steadiness",
        "Conscientiousness",
    ]


@pytest.mark.asyncio
async def test_big_five_stays_single_character_by_default(monkeypatch):
    """Big Five is canonically blended but NOT in the default pilot -> unchanged."""
    _set_pilot_allowlist(monkeypatch, ["disc"])
    single, blended = _wire_writers(monkeypatch)

    out = await _run_node(monkeypatch, "Big Five")

    assert single.called is True
    assert blended.called is False
    # Byte-identical single-character: neither new field is set.
    assert out["final_result"].result_kind is None
    assert out["final_result"].profile is None


@pytest.mark.asyncio
async def test_harry_potter_stays_single_character(monkeypatch):
    _set_pilot_allowlist(monkeypatch, ["disc"])
    single, blended = _wire_writers(monkeypatch)

    out = await _run_node(monkeypatch, "Harry Potter")

    assert single.called is True
    assert blended.called is False
    assert out["final_result"].result_kind is None
    assert out["final_result"].profile is None


@pytest.mark.asyncio
async def test_widened_allowlist_routes_big_five_to_blended(monkeypatch):
    _set_pilot_allowlist(monkeypatch, ["disc", "big five"])
    single, blended = _wire_writers(monkeypatch)

    out = await _run_node(monkeypatch, "Big Five")

    assert blended.called is True
    assert single.called is False
    assert out["final_result"].result_kind == "blended_profile"
