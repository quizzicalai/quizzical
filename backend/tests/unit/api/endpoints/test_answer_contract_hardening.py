# backend/tests/unit/api/endpoints/test_answer_contract_hardening.py
"""Deep-review #4 + #6 (backend halves) — the answer contract can no longer
silently lose or misattribute a user's answer.

#4: /quiz/next duplicates are 202-idempotent ONLY when they resolve to the
    same recorded answer; a duplicate carrying a DIFFERENT option is a client
    desync and surfaces 409 with the expected index so the FE can resync.
#6: /quiz/status serves strictly from the server's own answer count
    (``answered_idx``); an inflated client ``known_questions_count`` can no
    longer make the server skip an on-screen unanswered question (which
    misattributed the next click to the skipped question).
"""
import uuid

import pytest
from fastapi import HTTPException

from app.api.endpoints.quiz import (
    _display_option_order,
    _validate_and_record_answer,
)
from app.main import API_PREFIX
from app.models.api import NextQuestionRequest

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


# ---------------------------------------------------------------------------
# #4 — conflicting duplicate → 409; identical duplicate → idempotent 202 path
# ---------------------------------------------------------------------------

_Q_TEXT = "Which snack calls to you?"
_RAW = ["apple", "banana", "cracker", "dumpling"]


def _state_with_recorded_answer(recorded_slot: int) -> tuple[dict, int]:
    """One 4-option question, already answered at DISPLAYED slot
    ``recorded_slot``. Returns (state_dict, canonical_index_recorded)."""
    order = _display_option_order(1, _Q_TEXT, len(_RAW))
    canonical = order[recorded_slot]
    q = {"question_text": _Q_TEXT, "options": [{"text": t} for t in _RAW]}
    state = {
        "quiz_history": [
            {
                "question_index": 0,
                "question_text": _Q_TEXT,
                "answer_text": _RAW[canonical],
                "option_index": canonical,
            }
        ],
        "generated_questions": [q, {"question_text": "Q2?", "options": [{"text": "x"}, {"text": "y"}]}],
        "messages": [],
    }
    return state, canonical


def test_duplicate_with_same_option_stays_idempotent():
    state, _ = _state_with_recorded_answer(recorded_slot=1)
    req = NextQuestionRequest(quiz_id=uuid.uuid4(), question_index=0, option_index=1)
    with pytest.raises(ValueError, match="DUPLICATE"):
        _validate_and_record_answer(state, req)


def test_duplicate_with_different_option_conflicts_409():
    state, _ = _state_with_recorded_answer(recorded_slot=1)
    req = NextQuestionRequest(quiz_id=uuid.uuid4(), question_index=0, option_index=2)
    with pytest.raises(HTTPException) as exc:
        _validate_and_record_answer(state, req)
    assert exc.value.status_code == 409
    # The detail carries the resync hint: the next expected question_index.
    assert "question_index is 1" in str(exc.value.detail)


def test_duplicate_with_unresolvable_payload_fails_open_to_duplicate():
    # Garbage option_index on a duplicate keeps the historical fail-open 202
    # path (ValueError DUPLICATE), not a new 4xx surface.
    state, _ = _state_with_recorded_answer(recorded_slot=1)
    req = NextQuestionRequest(quiz_id=uuid.uuid4(), question_index=0, option_index=99)
    with pytest.raises(ValueError, match="DUPLICATE"):
        _validate_and_record_answer(state, req)


# ---------------------------------------------------------------------------
# #6 — an inflated known_questions_count cannot skip the unanswered question
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("override_redis_dep")
async def test_status_reserves_current_question_when_client_overclaims(
    async_client, fake_redis
):
    """answered=1, 3 questions generated, client claims known=2 (it has SEEN
    question 2 but not answered it — e.g. a poll raced the on-screen
    question). The server must RE-SERVE question index 1, not skip to 2."""
    quiz_id = uuid.uuid4()
    state = make_questions_state(
        quiz_id=quiz_id, questions=["Q0", "Q1", "Q2"], answers=[0]
    )
    seed_quiz_state(fake_redis, quiz_id, state)

    response = await async_client.get(
        f"{api}/quiz/status/{quiz_id}",
        params=status_params(known_questions_count=2),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "active"
    assert data["type"] == "question"
    assert data["data"]["text"] == "Q1"
    # The served ordinal is the unanswered question's (1-based) number.
    assert data["data"]["questionNumber"] == 2
