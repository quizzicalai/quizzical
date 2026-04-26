"""Iter 4 — verify the transport model can validate real runtime state.

Static field-name parity (iter 3) is necessary but not sufficient. The
transport model must also accept the *types* the runtime nodes actually
write. This catches drift like ``synopsis: Synopsis`` vs ``synopsis: dict``
that field-name comparison alone cannot detect.
"""

from __future__ import annotations

import uuid

from langchain_core.messages import AIMessage

from app.agent.schemas import (
    AgentGraphStateModel,
    CharacterProfile,
    QuestionAnswer,
    QuizQuestion,
    Synopsis,
)


def _runtime_like_state() -> dict:
    """Produce a state shape that mirrors what real graph nodes return."""
    return {
        "session_id": uuid.uuid4(),
        "trace_id": "trace-xyz",
        "category": "Star Trek Captains",
        "messages": [
            # AIMessage is the runtime type but transport stores plain dicts.
            # The transport model is configured with messages: List[Dict].
            AIMessage(content="planned").model_dump(),
        ],
        "is_error": False,
        "error_message": None,
        "error_count": 0,
        "outcome_kind": "characters",
        "creativity_mode": "balanced",
        "topic_analysis": {"intent": "identify", "domain": "tv"},
        "synopsis": Synopsis(title="Quiz: Star Trek Captains", summary="..."),
        "ideal_archetypes": ["Picard", "Janeway"],
        "generated_characters": [
            CharacterProfile(name="Picard", short_description="diplomat", profile_text="..."),
        ],
        "generated_questions": [
            QuizQuestion(question_text="Tea?", options=[{"text": "Earl Grey, hot"}]),
        ],
        "agent_plan": {"title": "Quiz: Star Trek Captains", "synopsis": "...", "ideal_archetypes": ["Picard"]},
        "quiz_history": [
            QuestionAnswer(question_index=0, question_text="Tea?", answer_text="Earl Grey, hot"),
        ],
        "baseline_count": 1,
        "baseline_ready": True,
        "ready_for_questions": True,
        "should_finalize": False,
        "current_confidence": 0.42,
        "rag_context": [{"source": "wiki", "snippet": "..."}],
        "final_result": None,
        "last_served_index": 0,
    }


def test_agent_graph_state_model_validates_runtime_shape() -> None:
    """The transport model must validate a realistic runtime payload."""
    payload = _runtime_like_state()
    model = AgentGraphStateModel.model_validate(payload)
    # Round-trip preserves identifiers.
    assert model.category == "Star Trek Captains"
    assert model.baseline_ready is True
    assert model.ready_for_questions is True
    assert model.synopsis is not None and model.synopsis.title.startswith("Quiz:")
    assert len(model.generated_characters) == 1
    assert len(model.generated_questions) == 1
    assert model.rag_context == [{"source": "wiki", "snippet": "..."}]


def test_agent_graph_state_model_round_trip_dict() -> None:
    """``model_dump`` then ``model_validate`` should be lossless."""
    original = AgentGraphStateModel.model_validate(_runtime_like_state())
    dumped = original.model_dump(mode="json")
    restored = AgentGraphStateModel.model_validate(dumped)
    assert restored.model_dump(mode="json") == dumped


def test_agent_graph_state_model_rejects_unknown_field() -> None:
    """``extra='forbid'`` should reject typos and stray keys."""
    payload = _runtime_like_state()
    payload["totally_unknown_field"] = 123
    try:
        AgentGraphStateModel.model_validate(payload)
    except Exception:
        return
    raise AssertionError("Expected ValidationError for unknown field")
