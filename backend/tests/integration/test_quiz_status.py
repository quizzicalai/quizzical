# backend/tests/integration/test_quiz_status.py

import json
import uuid

import pytest

from app.main import API_PREFIX
from app.models.db import SessionHistory, SessionQuestions
from tests.fixtures.redis_fixtures import seed_quiz_state
from tests.helpers.sample_payloads import status_params
from tests.helpers.state_builders import (
    make_finished_state,
    make_questions_state,
    make_synopsis_state,
)


@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_status_processing_when_no_questions_yet(client, fake_redis):
    """
    If state exists but no questions generated (e.g. start phase), returns status=processing.
    """
    api = API_PREFIX.rstrip("/")
    quiz_id = uuid.uuid4()

    state = make_synopsis_state(quiz_id=quiz_id)
    seed_quiz_state(fake_redis, quiz_id, state)

    resp = await client.get(f"{api}/quiz/status/{quiz_id}", params=status_params())
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "processing"
    assert body["quizId"] == str(quiz_id)

@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_status_returns_next_question(client, fake_redis, fake_cache_store):
    """
    If questions exist and client knows fewer than available, returns the next question.
    Also verifies last_served_index is updated in Redis.
    """
    api = API_PREFIX.rstrip("/")
    quiz_id = uuid.uuid4()

    # Setup: 2 Questions available. Client knows 0.
    state = make_questions_state(
        quiz_id=quiz_id,
        questions=["Q1", "Q2"],
        answers=[]
    )
    state["last_served_index"] = -1
    seed_quiz_state(fake_redis, quiz_id, state)

    # Client asks with known_questions_count=0 -> expects Q1 (index 0)
    resp = await client.get(f"{api}/quiz/status/{quiz_id}", params=status_params(known_questions_count=0))
    assert resp.status_code == 200

    body = resp.json()
    assert body["status"] == "active"
    assert body["type"] == "question"
    assert body["data"]["text"] == "Q1"

    # Verify last_served_index updated to 0 in Redis
    key = f"quiz_session:{quiz_id}"
    cached = json.loads(fake_cache_store.get(key))
    assert cached["last_served_index"] == 0

@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_status_returns_finished_result(client, fake_redis):
    """
    If final_result is present in state, returns status=finished with result payload.
    """
    api = API_PREFIX.rstrip("/")
    quiz_id = uuid.uuid4()

    state = make_finished_state(
        quiz_id=quiz_id,
        result={"title": "You are a Winner", "description": "Great job."}
    )
    seed_quiz_state(fake_redis, quiz_id, state)

    resp = await client.get(f"{api}/quiz/status/{quiz_id}")
    assert resp.status_code == 200

    body = resp.json()
    assert body["status"] == "finished"
    assert body["type"] == "result"
    assert body["data"]["title"] == "You are a Winner"


# ---------------------------------------------------------------------------
# P1 — Redis live-state miss (TTL expiry / eviction) rehydrates from Postgres
# instead of 404-ing and permanently losing the quiz.
# ---------------------------------------------------------------------------

@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_status_rehydrates_question_from_db_on_cache_miss(client, sqlite_db_session):
    """Redis state gone but durable DB snapshots present -> serve the question."""
    api = API_PREFIX.rstrip("/")
    quiz_id = uuid.uuid4()

    # NOTE: deliberately do NOT seed Redis -> get_quiz_state returns None.
    sqlite_db_session.add(
        SessionHistory(
            session_id=quiz_id,
            category="Cats",
            category_synopsis={"title": "Quiz: Cats", "summary": "S"},
            character_set=[{"name": "Alpha", "short_description": "", "profile_text": "x", "image_url": None}],
            final_result=None,
            session_transcript=[],
        )
    )
    sqlite_db_session.add(
        SessionQuestions(
            session_id=quiz_id,
            baseline_questions={
                "questions": [
                    {"question_text": "Rehydrated Q1?", "options": [{"text": "a"}, {"text": "b"}]}
                ]
            },
            properties={"baseline_count": 1},
        )
    )
    await sqlite_db_session.commit()

    resp = await client.get(f"{api}/quiz/status/{quiz_id}", params=status_params(known_questions_count=0))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "active"
    assert body["type"] == "question"
    assert body["data"]["text"] == "Rehydrated Q1?"


@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_status_rehydrates_finished_result_from_db_on_cache_miss(client, sqlite_db_session):
    """A finished quiz whose Redis state expired still returns its result."""
    api = API_PREFIX.rstrip("/")
    quiz_id = uuid.uuid4()

    sqlite_db_session.add(
        SessionHistory(
            session_id=quiz_id,
            category="Cats",
            category_synopsis={"title": "Quiz: Cats", "summary": "S"},
            character_set=[{"name": "Alpha", "short_description": "", "profile_text": "x", "image_url": None}],
            final_result={"title": "You are Alpha", "description": "d" * 400, "image_url": None},
            session_transcript=[],
        )
    )
    await sqlite_db_session.commit()

    resp = await client.get(f"{api}/quiz/status/{quiz_id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "finished"
    assert body["type"] == "result"
    assert body["data"]["title"] == "You are Alpha"


@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_status_404_when_neither_redis_nor_db(client):
    """No Redis state and no DB row -> genuine 404."""
    api = API_PREFIX.rstrip("/")
    quiz_id = uuid.uuid4()
    resp = await client.get(f"{api}/quiz/status/{quiz_id}")
    assert resp.status_code == 404
