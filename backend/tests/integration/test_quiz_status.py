# backend/tests/integration/test_quiz_status.py

import json
import uuid
import pytest

from app.main import API_PREFIX
from tests.fixtures.agent_graph_fixtures import use_fake_agent_graph
from tests.fixtures.redis_fixtures import (
    override_redis_dep,
    fake_cache_store,
    fake_redis,
    seed_quiz_state,
)


@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_status_404_when_session_missing(async_client):
    api = API_PREFIX.rstrip("/")
    missing = str(uuid.uuid4())
    r = await async_client.get(f"{api}/quiz/status/{missing}")
    assert r.status_code == 404
    assert "not found" in r.text.lower()


@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_status_processing_before_baseline_questions(async_client):
    """
    Start + proceed (background work is *captured* elsewhere; we don't run it),
    so no baseline questions yet → status should be 'processing'.
    """
    api = API_PREFIX.rstrip("/")

    # Start a new quiz (persists initial state)
    start_payload = {"category": "Cats", "cf-turnstile-response": "test-token"}
    sr = await async_client.post(f"{api}/quiz/start?_a=dev&_k=dev", json=start_payload)
    assert sr.status_code == 201, sr.text
    quiz_id = sr.json()["quizId"]

    # Proceed (opens gate; background not executed in this test)
    pr = await async_client.post(f"{api}/quiz/proceed", json={"quiz_id": quiz_id})
    assert pr.status_code == 202, pr.text

    # Poll status → still processing (no questions generated yet)
    st = await async_client.get(f"{api}/quiz/status/{quiz_id}")
    assert st.status_code == 200
    body = st.json()
    assert body["status"] == "processing"
    assert body["quizId"] == quiz_id


@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_status_returns_next_unseen_question_and_updates_last_served_index(
    async_client, fake_cache_store, fake_redis
):
    """
    Server should serve the *next unseen* index based on max(answered, known_questions_count),
    never going backwards; it should also persist last_served_index.
    """
    api = API_PREFIX.rstrip("/")

    quiz_id = uuid.uuid4()
    qs = [
        {"question_text": "Q0", "options": [{"text": "A"}, {"text": "B"}]},
        {"question_text": "Q1", "options": [{"text": "C"}, {"text": "D"}]},
        {"question_text": "Q2", "options": [{"text": "E"}, {"text": "F"}]},
    ]
    # One answer recorded → answered_idx=1. known_questions_count will also be 1.
    seed_quiz_state(
        fake_redis,
        quiz_id,
        {
            "session_id": str(quiz_id),
            "trace_id": "t-1",
            "category": "Cats",
            "messages": [],
            "category_synopsis": {"title": "Quiz: Cats", "summary": "…"},
            "generated_characters": [{"name": "The Optimist", "short_description": "", "profile_text": ""}],
            "generated_questions": qs,
            "quiz_history": [{"question_index": 0, "question_text": "Q0", "answer_text": "A", "option_index": 0}],
            "baseline_count": 3,
            "baseline_ready": True,
            "ready_for_questions": True,
            "is_error": False,
            "error_message": None,
            "error_count": 0,
            "last_served_index": 0,
        },
    )

    # known_questions_count=1, answered_idx=1 → target_index = 1 → serve Q1
    r = await async_client.get(f"{api}/quiz/status/{quiz_id}?known_questions_count=1")
    assert r.status_code == 200
    payload = r.json()
    assert payload["status"] == "active"
    assert payload["type"] == "question"
    q = payload["data"]
    # API model exposes 'text' + 'options'
    assert q["text"] == "Q1"
    assert isinstance(q["options"], list) and len(q["options"]) == 2 and q["options"][0]["text"] == "C"

    # It should persist last_served_index = 1
    key = f"quiz_session:{quiz_id}"
    stored_raw = fake_cache_store.get(key)
    assert stored_raw, "Expected state to be saved back to cache"
    stored = json.loads(stored_raw if isinstance(stored_raw, str) else stored_raw.decode("utf-8"))
    assert stored.get("last_served_index") == 1


@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_status_processing_when_client_knows_all_available(async_client, fake_redis):
    """
    If client says they've seen as many as exist (known == server count),
    there is nothing new to serve → 'processing'.
    """
    api = API_PREFIX.rstrip("/")

    quiz_id = uuid.uuid4()
    qs = [
        {"question_text": "Q0", "options": [{"text": "A"}]},
        {"question_text": "Q1", "options": [{"text": "B"}]},
    ]
    seed_quiz_state(
        fake_redis,
        quiz_id,
        {
            "session_id": str(quiz_id),
            "trace_id": "t-2",
            "category": "Cats",
            "messages": [],
            "category_synopsis": {"title": "Quiz: Cats", "summary": "…"},
            "generated_characters": [{"name": "The Analyst", "short_description": "", "profile_text": ""}],
            "generated_questions": qs,
            "quiz_history": [],
            "baseline_count": 2,
            "baseline_ready": True,
            "ready_for_questions": True,
            "is_error": False,
            "error_message": None,
            "error_count": 0,
            "last_served_index": -1,
        },
    )

    r = await async_client.get(f"{api}/quiz/status/{quiz_id}?known_questions_count=2")
    assert r.status_code == 200
    payload = r.json()
    assert payload["status"] == "processing"
    assert payload["quizId"] == str(quiz_id)


@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_status_finished_when_final_result_present(async_client, fake_redis):
    """
    If the state has final_result, /status returns the finished payload immediately.
    """
    api = API_PREFIX.rstrip("/")

    quiz_id = uuid.uuid4()
    seed_quiz_state(
        fake_redis,
        quiz_id,
        {
            "session_id": str(quiz_id),
            "trace_id": "t-3",
            "category": "Cats",
            "messages": [],
            "category_synopsis": {"title": "Quiz: Cats", "summary": "…"},
            "generated_characters": [{"name": "The Optimist", "short_description": "", "profile_text": ""}],
            "generated_questions": [],
            "quiz_history": [{"question_index": 0, "question_text": "Q0", "answer_text": "A"}],
            "baseline_count": 1,
            "baseline_ready": True,
            "ready_for_questions": True,
            "final_result": {"title": "You are The Optimist", "description": "Cheery and upbeat.", "image_url": None},
            "is_error": False,
            "error_message": None,
            "error_count": 0,
        },
    )

    r = await async_client.get(f"{api}/quiz/status/{quiz_id}")
    assert r.status_code == 200
    payload = r.json()
    assert payload["status"] == "finished"
    assert payload["type"] == "result"
    result = payload["data"]
    assert result["title"].startswith("You are The Optimist")
