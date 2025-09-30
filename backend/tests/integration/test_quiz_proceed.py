import json
import uuid
import pytest

from app.main import API_PREFIX
from app.api.endpoints.quiz import run_agent_in_background
from tests.fixtures.agent_graph_fixtures import use_fake_agent_graph
from tests.fixtures.redis_fixtures import (
    override_redis_dep,
    fake_cache_store,
    fake_redis,
    seed_quiz_state,
)
from tests.fixtures.background_tasks import capture_background_tasks


@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_proceed_202_marks_ready_and_schedules_background(
    async_client, fake_cache_store, capture_background_tasks
):
    api = API_PREFIX.rstrip("/")

    # Start → create a real session persisted in cache
    start_payload = {"category": "Cats", "cf-turnstile-response": "test-token"}
    r = await async_client.post(f"{api}/quiz/start?_a=dev&_k=dev", json=start_payload)
    assert r.status_code == 201, r.text
    quiz_id = r.json()["quizId"]

    # Sanity: confirm it's in cache pre-proceed
    key = f"quiz_session:{quiz_id}"
    before_raw = fake_cache_store.get(key)
    assert before_raw
    before = json.loads(before_raw if isinstance(before_raw, str) else before_raw.decode("utf-8"))
    assert before.get("ready_for_questions") is False

    # Proceed
    pr = await async_client.post(f"{api}/quiz/proceed", json={"quiz_id": quiz_id})
    assert pr.status_code == 202, pr.text
    body = pr.json()
    assert body["status"] == "processing"
    assert body["quizId"] == quiz_id

    # 1) State persisted with gate opened (before scheduling)
    after_raw = fake_cache_store.get(key)
    assert after_raw
    after = json.loads(after_raw if isinstance(after_raw, str) else after_raw.decode("utf-8"))
    assert after.get("ready_for_questions") is True
    # baseline flags shouldn’t change here
    assert after.get("baseline_ready") in (False, None)
    assert int(after.get("baseline_count") or 0) == int(before.get("baseline_count") or 0)

    # 2) Background task scheduled once with correct callable & args
    assert len(capture_background_tasks) == 1
    func, args, kwargs = capture_background_tasks[0]
    assert func is run_agent_in_background
    # state dict passed to task has the gate open
    task_state = args[0]
    assert isinstance(task_state, dict)
    # session_id may be uuid or string depending on hydration; compare stringified
    assert str(task_state.get("session_id")) == str(quiz_id)
    assert task_state.get("ready_for_questions") is True
    # agent_graph should duck-type
    agent_graph = args[2]
    assert all(hasattr(agent_graph, m) for m in ("ainvoke", "astream", "aget_state"))


@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_proceed_404_when_session_missing(async_client):
    api = API_PREFIX.rstrip("/")
    missing_id = str(uuid.uuid4())
    pr = await async_client.post(f"{api}/quiz/proceed", json={"quiz_id": missing_id})
    assert pr.status_code == 404
    assert "not found" in pr.text.lower()


@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_proceed_twice_schedules_each_time_and_keeps_state(
    async_client, fake_cache_store, capture_background_tasks, fake_redis
):
    api = API_PREFIX.rstrip("/")

    # Seed a minimal valid state directly (faster than calling /quiz/start twice)
    quiz_id = uuid.uuid4()
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
            "generated_questions": [],
            "quiz_history": [],
            "baseline_count": 0,
            "baseline_ready": False,
            "ready_for_questions": False,
            "is_error": False,
            "error_message": None,
            "error_count": 0,
        },
    )

    # First proceed
    r1 = await async_client.post(f"{api}/quiz/proceed", json={"quiz_id": str(quiz_id)})
    assert r1.status_code == 202

    # Second proceed
    r2 = await async_client.post(f"{api}/quiz/proceed", json={"quiz_id": str(quiz_id)})
    assert r2.status_code == 202

    # Two background tasks captured
    assert len(capture_background_tasks) == 2
    assert all(call[0] is run_agent_in_background for call in capture_background_tasks)

    # State remains with gate open; baseline flags preserved
    key = f"quiz_session:{quiz_id}"
    raw = fake_cache_store.get(key)
    assert raw
    doc = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
    assert doc.get("ready_for_questions") is True
    assert doc.get("baseline_ready") is False
    assert int(doc.get("baseline_count") or 0) == 0
