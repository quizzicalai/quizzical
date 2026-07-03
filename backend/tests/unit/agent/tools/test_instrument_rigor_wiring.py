# backend/tests/unit/agent/tools/test_instrument_rigor_wiring.py
"""INSTRUMENT RIGOR wiring — the conditional block threads through QG/NQG/graph.

Asserts the end-to-end contract of the feature (owner blackbox #5, 2026-07-02):

* ``generate_baseline_questions`` fills ``{instrument_rigor}`` with the rigor
  block for an instrument topic (MBTI) and with "" for a whimsical topic, and
  tags each surviving question with its normalized ``dimension``.
* ``generate_next_question`` accepts ``asked_dimensions``, renders the
  coverage report targeting the LEAST-COVERED dimension, and carries the
  model's dimension tag onto the returned ``QuizQuestion``.
* The graph's adaptive node extracts the dimension tags from state and passes
  them to the tool; ``_quiz_question_from_obj`` preserves the tag.
* The dumped state dict for non-instrument questions is unchanged (the
  ``dimension`` key is absent, not null).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent.schemas import (
    QuestionOption,
    QuestionOut,
    QuizQuestion,
    build_question_out_jsonschema,
)
from app.agent.tools import content_creation_tools as ctools

pytestmark = [pytest.mark.unit, pytest.mark.no_tool_stubs]


class CapturePrompt:
    """Prompt stand-in that records the payload passed to ``invoke``."""

    def __init__(self):
        self.payload = None

    def invoke(self, payload):
        self.payload = payload
        return SimpleNamespace(messages=["dummy"])


def _analysis_for(category: str) -> dict:
    return {
        "normalized_category": category,
        "outcome_kind": "types",
        "creativity_mode": "factual",
        "intent": "identify",
    }


# ---------------------------------------------------------------------------
# generate_baseline_questions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_baseline_qg_injects_rigor_block_and_tags_dimensions(monkeypatch):
    prompt = CapturePrompt()
    monkeypatch.setattr(
        ctools.prompt_manager, "get_prompt", lambda name: prompt, raising=True
    )

    qlist = SimpleNamespace(
        questions=[
            QuestionOut(
                question_text="When plans change last minute, you usually…",
                options=[QuestionOption(text="Adapt on the fly"), QuestionOption(text="Re-plan first")],
                dimension="j/p",  # loose casing → normalized to "J/P"
            ),
            QuestionOut(
                question_text="At a party you tend to…",
                options=[QuestionOption(text="Work the room"), QuestionOption(text="Find one good conversation")],
                dimension="Extraversion vs Introversion",  # name → "E/I"
            ),
        ]
    )

    async def fake_invoke_structured(**kwargs):
        return qlist

    monkeypatch.setattr(ctools, "invoke_structured", fake_invoke_structured, raising=True)
    monkeypatch.setattr(ctools, "jsonschema_for", lambda *a, **k: {}, raising=True)

    out = await ctools.generate_baseline_questions.ainvoke(
        {
            "category": "Myers-Briggs Personality Types",
            "character_profiles": [],
            "synopsis": {"title": "Quiz: Myers-Briggs Personality Types"},
            "analysis": _analysis_for("Myers-Briggs Personality Types"),
            "num_questions": 2,
        }
    )

    # The prompt received a NON-empty rigor block naming the instrument.
    rigor = prompt.payload["instrument_rigor"]
    assert "INSTRUMENT RIGOR — Myers-Briggs Personality Types" in rigor
    assert '"E/I"' in rigor and '"J/P"' in rigor

    # Questions carry normalized dimension tags.
    assert [q.dimension for q in out] == ["J/P", "E/I"]


@pytest.mark.asyncio
async def test_baseline_qg_whimsical_topic_gets_empty_block_and_no_tags(monkeypatch):
    prompt = CapturePrompt()
    monkeypatch.setattr(
        ctools.prompt_manager, "get_prompt", lambda name: prompt, raising=True
    )

    qlist = SimpleNamespace(
        questions=[
            QuestionOut(
                question_text="Pick a bridge to lurk under.",
                options=[QuestionOption(text="Stone"), QuestionOption(text="Wooden")],
                # Even if the model volunteers a dimension, a non-instrument
                # topic must NOT tag it (spec is None → tag dropped).
                dimension="E/I",
            ),
        ]
    )

    async def fake_invoke_structured(**kwargs):
        return qlist

    monkeypatch.setattr(ctools, "invoke_structured", fake_invoke_structured, raising=True)
    monkeypatch.setattr(ctools, "jsonschema_for", lambda *a, **k: {}, raising=True)

    out = await ctools.generate_baseline_questions.ainvoke(
        {
            "category": "what type of troll am i",
            "character_profiles": [],
            "synopsis": {"title": "Quiz: what type of troll am i"},
            "analysis": {
                "normalized_category": "what type of troll am i",
                "outcome_kind": "types",
                "creativity_mode": "whimsical",
                "intent": "identify",
            },
            "num_questions": 1,
        }
    )

    assert prompt.payload["instrument_rigor"] == ""
    assert out[0].dimension is None
    # Non-instrument state dict is unchanged: no "dimension" key at all.
    assert "dimension" not in out[0].model_dump(mode="json", exclude_none=True)


# ---------------------------------------------------------------------------
# generate_next_question
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nqg_targets_least_covered_dimension_and_tags_result(monkeypatch):
    prompt = CapturePrompt()
    monkeypatch.setattr(
        ctools.prompt_manager, "get_prompt", lambda name: prompt, raising=True
    )

    q_out = QuestionOut(
        question_text="A friend asks for tough feedback. You…",
        options=[
            QuestionOption(text="Give the unvarnished analysis"),
            QuestionOption(text="Soften it to protect the friendship"),
        ],
        dimension="T/F",
    )

    async def fake_invoke_structured(**kwargs):
        return q_out

    monkeypatch.setattr(ctools, "invoke_structured", fake_invoke_structured, raising=True)
    monkeypatch.setattr(ctools, "jsonschema_for", lambda *a, **k: {}, raising=True)

    result = await ctools.generate_next_question.ainvoke(
        {
            "quiz_history": [{"question_text": "Q1", "answer_text": "A"}],
            "character_profiles": [],
            "synopsis": {"title": "Quiz: Myers-Briggs Personality Types"},
            "analysis": _analysis_for("Myers-Briggs Personality Types"),
            "asked_dimensions": ["E/I", "E/I", "S/N"],
        }
    )

    rigor = prompt.payload["instrument_rigor"]
    assert "Coverage so far" in rigor
    assert "UNDER-COVERED dimensions: T/F, J/P" in rigor
    assert 'MUST probe "T/F"' in rigor

    assert isinstance(result, QuizQuestion)
    assert result.dimension == "T/F"


@pytest.mark.asyncio
async def test_nqg_non_instrument_topic_gets_empty_block(monkeypatch):
    prompt = CapturePrompt()
    monkeypatch.setattr(
        ctools.prompt_manager, "get_prompt", lambda name: prompt, raising=True
    )

    q_out = QuestionOut(
        question_text="Pick a snack.",
        options=[QuestionOption(text="Goats"), QuestionOption(text="Billy goats")],
    )

    async def fake_invoke_structured(**kwargs):
        return q_out

    monkeypatch.setattr(ctools, "invoke_structured", fake_invoke_structured, raising=True)
    monkeypatch.setattr(ctools, "jsonschema_for", lambda *a, **k: {}, raising=True)

    result = await ctools.generate_next_question.ainvoke(
        {
            "quiz_history": [],
            "character_profiles": [],
            "synopsis": {"title": "Quiz: what type of troll am i"},
            "analysis": {
                "normalized_category": "what type of troll am i",
                "outcome_kind": "types",
                "creativity_mode": "whimsical",
                "intent": "identify",
            },
        }
    )

    assert prompt.payload["instrument_rigor"] == ""
    assert result.dimension is None


# ---------------------------------------------------------------------------
# Graph wiring
# ---------------------------------------------------------------------------


def test_graph_quiz_question_from_obj_preserves_dimension():
    from app.agent.graph import _quiz_question_from_obj

    q = _quiz_question_from_obj(
        {
            "question_text": "Q",
            "options": [{"text": "A"}, {"text": "B"}],
            "dimension": "S/N",
        }
    )
    assert q.dimension == "S/N"

    q2 = _quiz_question_from_obj(
        {"question_text": "Q", "options": [{"text": "A"}, {"text": "B"}]}
    )
    assert q2.dimension is None
    assert "dimension" not in q2.model_dump(mode="json", exclude_none=True)


@pytest.mark.asyncio
async def test_graph_adaptive_node_passes_asked_dimensions(monkeypatch):
    import app.agent.graph as graph_mod

    captured: dict = {}

    class FakeTool:
        async def ainvoke(self, payload):
            captured.update(payload)
            return QuizQuestion(
                question_text="Next?",
                options=[{"text": "A"}, {"text": "B"}],
                dimension="T/F",
            )

    monkeypatch.setattr(
        graph_mod, "tool_generate_next_question", FakeTool(), raising=True
    )

    state = {
        "session_id": "s",
        "trace_id": "t",
        "synopsis": {"title": "Quiz: MBTI", "summary": ""},
        "generated_characters": [],
        "quiz_history": [],
        "topic_analysis": {},
        "generated_questions": [
            {"question_text": "Q1", "options": [{"text": "A"}], "dimension": "E/I"},
            {"question_text": "Q2", "options": [{"text": "A"}]},  # untagged
            {"question_text": "Q3", "options": [{"text": "A"}], "dimension": "S/N"},
        ],
    }

    out = await graph_mod._generate_adaptive_question_node(state)

    assert captured["asked_dimensions"] == ["E/I", "S/N"]
    # The new question is appended in state shape with its dimension tag.
    assert out["generated_questions"][-1]["dimension"] == "T/F"


# ---------------------------------------------------------------------------
# Schema: additive + optional
# ---------------------------------------------------------------------------


def test_question_out_jsonschema_has_optional_dimension():
    env = build_question_out_jsonschema()
    props = env["schema"]["properties"]
    assert "dimension" in props
    # NOT required — non-instrument topics never emit it.
    assert "dimension" not in env["schema"]["required"]


def test_quiz_question_dimension_roundtrip():
    q = QuizQuestion(
        question_text="Q", options=[{"text": "A"}, {"text": "B"}], dimension="D"
    )
    dumped = q.model_dump(mode="json", exclude_none=True)
    assert dumped["dimension"] == "D"
    assert QuizQuestion.model_validate(dumped).dimension == "D"
