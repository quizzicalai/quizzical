# backend/tests/unit/api/endpoints/test_status_progress.py
"""UX-2026-07-02 — real progress/closeness payload on /quiz/status.

Owner blackbox failure (twice): the quiz card's upper-right cue "kept saying
the same thing — gave no indication (qualitative or otherwise) of its level of
confidence, despite many questions being answered." Half of that bug was the
wire contract: the served question carried only an ordinal + a sporadic
confidence, so the FE had nothing honest to escalate with.

These tests pin the new contract:

1. ``_format_next_question`` carries ``answered_count`` and ``max_questions``
   through to the wire (camelCase ``answeredCount`` / ``maxQuestions``), and
   drops invalid values to ``None`` instead of crashing the serve path.
2. ``_effective_max_questions`` derives the SAME topic-aware hard cap the
   agent graph uses to force-finish (single source of truth), never above the
   owner ceiling of 24, and fails open to ``None``.
3. GET /quiz/status question payloads carry the real ``answeredCount``
   (server-recorded answers) and the effective ``maxQuestions`` for the
   quiz's category.
"""
from __future__ import annotations

import uuid

import pytest

from app.agent.schemas import QuizQuestion
from app.api.endpoints.quiz import (
    _effective_max_questions,
    _format_next_question,
)
from app.main import API_PREFIX

# Fixtures
from tests.fixtures.redis_fixtures import (  # noqa: F401
    fake_cache_store,
    fake_redis,
    override_redis_dep,
    seed_quiz_state,
)

# Helpers
from tests.helpers.sample_payloads import status_params
from tests.helpers.state_builders import make_questions_state

api = API_PREFIX.rstrip("/")
pytestmark = pytest.mark.anyio


def _make_question() -> QuizQuestion:
    return QuizQuestion(
        question_text="Pick a pace",
        options=[{"text": "steady"}, {"text": "sprint"}, {"text": "wander"}],
    )


# ---------------------------------------------------------------------------
# 1. _format_next_question — passthrough + validation + camelCase aliases
# ---------------------------------------------------------------------------


def test_format_next_question_carries_progress_fields():
    out = _format_next_question(
        _make_question(),
        question_number=7,
        confidence=0.4,
        answered_count=6,
        max_questions=12,
    )
    assert out.answered_count == 6
    assert out.max_questions == 12
    # answered_count is always question_number - 1 on the serve path.
    assert out.answered_count == out.question_number - 1

    dumped = out.model_dump(by_alias=True)
    assert dumped["answeredCount"] == 6
    assert dumped["maxQuestions"] == 12


def test_format_next_question_progress_fields_default_none():
    """Backwards compat: callers that don't pass the fields get None."""
    out = _format_next_question(_make_question(), question_number=1)
    assert out.answered_count is None
    assert out.max_questions is None


@pytest.mark.parametrize(
    "answered_count, max_questions",
    [(-1, 0), (None, None), ("6", "12"), (-5, -2)],
)
def test_format_next_question_drops_invalid_progress_values(
    answered_count, max_questions
):
    """Garbage must be dropped to None — never a crash, never a negative leak."""
    out = _format_next_question(
        _make_question(),
        question_number=1,
        answered_count=answered_count,
        max_questions=max_questions,
    )
    assert out.answered_count is None
    assert out.max_questions is None


def test_format_next_question_answered_count_zero_is_valid():
    """0 answered (first question) is a real value, not falsy-dropped."""
    out = _format_next_question(
        _make_question(), question_number=1, answered_count=0, max_questions=24
    )
    assert out.answered_count == 0
    assert out.max_questions == 24


# ---------------------------------------------------------------------------
# 2. _effective_max_questions — same bound the graph uses to stop
# ---------------------------------------------------------------------------


def test_effective_max_questions_matches_graph_bounds():
    from app.agent.graph import _effective_depth_bounds

    for category in (None, "Gilmore Girls", "DISC"):
        _eff_min, eff_max = _effective_depth_bounds(category)
        assert _effective_max_questions(category) == eff_max


def test_effective_max_questions_never_exceeds_owner_ceiling():
    got = _effective_max_questions("Some Casual Topic")
    assert got is not None
    assert 1 <= got <= 24


def test_effective_max_questions_tolerates_non_string_category():
    # Fail-open contract: junk categories still resolve (default bounds) or
    # None — never raise into the serve path.
    for junk in (123, {"a": 1}, "", "   "):
        got = _effective_max_questions(junk)
        assert got is None or 1 <= got <= 24


# ---------------------------------------------------------------------------
# 3. GET /quiz/status — question payload carries the closeness fields
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("override_redis_dep")
async def test_status_question_carries_progress_payload(async_client, fake_redis):
    """answered=1 of 3 generated → the served question (ordinal 2) reports
    answeredCount=1 and the topic-aware maxQuestions the graph enforces."""
    from app.agent.graph import _effective_depth_bounds

    quiz_id = uuid.uuid4()
    state = make_questions_state(
        quiz_id=quiz_id, questions=["Q0", "Q1", "Q2"], answers=[0]
    )
    seed_quiz_state(fake_redis, quiz_id, state)

    response = await async_client.get(
        f"{api}/quiz/status/{quiz_id}",
        params=status_params(known_questions_count=1),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "active"
    q = data["data"]
    assert q["questionNumber"] == 2
    assert q["answeredCount"] == 1

    _eff_min, expected_max = _effective_depth_bounds(state["category"])
    assert q["maxQuestions"] == expected_max
    assert 1 <= q["maxQuestions"] <= 24


@pytest.mark.usefixtures("override_redis_dep")
async def test_status_first_question_reports_zero_answered(async_client, fake_redis):
    quiz_id = uuid.uuid4()
    state = make_questions_state(quiz_id=quiz_id, questions=["Q0", "Q1"], answers=[])
    seed_quiz_state(fake_redis, quiz_id, state)

    response = await async_client.get(
        f"{api}/quiz/status/{quiz_id}", params=status_params()
    )

    assert response.status_code == 200
    q = response.json()["data"]
    assert q["questionNumber"] == 1
    assert q["answeredCount"] == 0
    assert isinstance(q["maxQuestions"], int)
