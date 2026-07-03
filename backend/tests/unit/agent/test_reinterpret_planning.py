# backend/tests/unit/agent/test_reinterpret_planning.py
"""
"Try a different interpretation" (owner blackbox, 2026-07-02) — planner-side
behaviour of the reinterpret reload.

Covers:
- plan_quiz appends the rejected-interpretations prompt block (as an extra
  final message) ONLY when rejections are supplied — a normal call's prompt
  is byte-for-byte unchanged;
- plan_quiz skips the canonical-set override on a reinterpret (forcing the
  canonical list back would return the rejected reading);
- _bootstrap_node threads state["rejected_interpretations"] into the planner
  tool payload and skips its own canonical override.
"""

import uuid
from types import SimpleNamespace

import pytest

import app.agent.graph as graph_mod
from app.agent.schemas import InitialPlan
from app.agent.tools import planning_tools as ptools

pytestmark = [pytest.mark.unit, pytest.mark.no_tool_stubs]

REJECTED = [
    "Quiz: Trolls — A quiz about grumpy bridge-dwelling trolls of folklore.",
    "Quiz: Internet Trolls — A quiz about online provocateurs.",
]

PRE_ANALYSIS = {
    "category": "Trolls",
    "outcome_kind": "types",
    "creativity_mode": "balanced",
    "intent": "identify",
}


def _spy_invoke_structured(monkeypatch, plan: InitialPlan | None = None) -> dict:
    """Capture the exact messages plan_quiz sends to the LLM."""
    captured: dict = {}

    async def _fake_llm(**kwargs):
        captured["messages"] = kwargs.get("messages")
        return plan or InitialPlan(
            title="Quiz: Trolls (the movie)",
            synopsis="A very different reading.",
            ideal_archetypes=["Poppy", "Branch", "Bridget", "King Gristle"],
        )

    monkeypatch.setattr(ptools, "invoke_structured", _fake_llm, raising=True)
    return captured


def _message_texts(messages) -> list[str]:
    out = []
    for m in messages or []:
        content = getattr(m, "content", None)
        if content is None and isinstance(m, dict):
            content = m.get("content")
        out.append(str(content))
    return out


# ---------------------------------------------------------------------------
# plan_quiz — prompt block
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_plan_quiz_appends_rejected_block(monkeypatch):
    captured = _spy_invoke_structured(monkeypatch)

    plan = await ptools.plan_quiz.ainvoke(
        {**PRE_ANALYSIS, "rejected_interpretations": REJECTED}
    )

    texts = _message_texts(captured["messages"])
    block = texts[-1]  # additive block rides as the FINAL message
    assert "REJECTED" in block
    for rejected in REJECTED:
        assert rejected in block
    # The instruction demands a genuinely different reading, not a rephrasing.
    assert "DIFFERENT reading" in block
    assert "NEVER rephrase" in block
    assert plan.title  # planner result still parses into an InitialPlan


@pytest.mark.asyncio
async def test_plan_quiz_block_matches_canonical_builder(monkeypatch):
    """The appended message is exactly build_rejected_interpretations_block."""
    captured = _spy_invoke_structured(monkeypatch)

    await ptools.plan_quiz.ainvoke(
        {**PRE_ANALYSIS, "rejected_interpretations": REJECTED}
    )

    texts = _message_texts(captured["messages"])
    assert texts[-1] == ptools.build_rejected_interpretations_block(REJECTED)


@pytest.mark.asyncio
async def test_plan_quiz_without_rejections_has_no_block(monkeypatch):
    """Normal start parity: no rejections -> the prompt gains NO extra message
    and never mentions rejected interpretations."""
    captured = _spy_invoke_structured(monkeypatch)

    await ptools.plan_quiz.ainvoke(dict(PRE_ANALYSIS))
    baseline_texts = _message_texts(captured["messages"])

    assert all("REJECTED" not in t for t in baseline_texts)

    # Empty/whitespace-only lists are treated exactly like None.
    await ptools.plan_quiz.ainvoke(
        {**PRE_ANALYSIS, "rejected_interpretations": ["", "   "]}
    )
    assert _message_texts(captured["messages"]) == baseline_texts


@pytest.mark.asyncio
async def test_plan_quiz_skips_canonical_override_on_reinterpret(monkeypatch):
    """A canonical topic's forced archetype list IS the default reading — it
    must not clobber the planner's new interpretation on a reinterpret."""
    monkeypatch.setattr(ptools, "canonical_for", lambda x: ["Alpha", "Beta"], raising=True)
    monkeypatch.setattr(ptools, "count_hint_for", lambda x: 2, raising=True)
    _spy_invoke_structured(
        monkeypatch,
        plan=InitialPlan(title="T", synopsis="S", ideal_archetypes=["New A", "New B"]),
    )

    plan = await ptools.plan_quiz.ainvoke(
        {**PRE_ANALYSIS, "rejected_interpretations": REJECTED[:1]}
    )
    assert plan.ideal_archetypes == ["New A", "New B"]

    # Without rejections the canonical override still applies (unchanged).
    plan_normal = await ptools.plan_quiz.ainvoke(dict(PRE_ANALYSIS))
    assert plan_normal.ideal_archetypes == ["Alpha", "Beta"]


# ---------------------------------------------------------------------------
# _bootstrap_node — state threading
# ---------------------------------------------------------------------------

def _patch_bootstrap_collaborators(monkeypatch, captured: dict) -> None:
    monkeypatch.setattr(
        graph_mod,
        "analyze_topic",
        lambda cat: {
            "normalized_category": cat,
            "outcome_kind": "types",
            "creativity_mode": "balanced",
        },
    )

    async def mock_plan(payload):
        captured["payload"] = dict(payload)
        return InitialPlan(
            title="Quiz: Trolls (the movie)",
            synopsis="A different reading.",
            ideal_archetypes=["Poppy", "Branch", "Bridget", "King Gristle"],
        )

    monkeypatch.setattr(graph_mod, "tool_plan_quiz", SimpleNamespace(ainvoke=mock_plan))

    async def mock_repair(arch, *args):
        return arch

    monkeypatch.setattr(graph_mod, "_repair_archetypes_if_needed", mock_repair)


@pytest.mark.asyncio
async def test_bootstrap_node_threads_rejected_into_planner(monkeypatch):
    captured: dict = {}
    _patch_bootstrap_collaborators(monkeypatch, captured)
    # Canonical set present: must be SKIPPED for a reinterpret.
    monkeypatch.setattr(graph_mod, "canonical_for", lambda x: ["Old A", "Old B"])

    state = {
        "session_id": uuid.uuid4(),
        "trace_id": "t",
        "messages": [SimpleNamespace(content="Trolls")],
        "rejected_interpretations": REJECTED,
    }
    out = await graph_mod._bootstrap_node(state)

    assert captured["payload"]["rejected_interpretations"] == REJECTED
    # Canonical override skipped: the planner's NEW reading survives.
    assert out["ideal_archetypes"] == ["Poppy", "Branch", "Bridget", "King Gristle"]


@pytest.mark.asyncio
async def test_bootstrap_node_normal_start_passes_none(monkeypatch):
    captured: dict = {}
    _patch_bootstrap_collaborators(monkeypatch, captured)
    monkeypatch.setattr(graph_mod, "canonical_for", lambda x: None)

    state = {
        "session_id": uuid.uuid4(),
        "trace_id": "t",
        "messages": [SimpleNamespace(content="Trolls")],
    }
    await graph_mod._bootstrap_node(state)

    assert captured["payload"]["rejected_interpretations"] is None
