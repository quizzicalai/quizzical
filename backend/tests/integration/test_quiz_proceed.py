# backend/tests/integration/test_quiz_proceed.py

import json
import uuid
import pytest

from app.main import API_PREFIX
from app.api.endpoints.quiz import run_agent_in_background
from tests.helpers.sample_payloads import proceed_payload
from tests.helpers.state_builders import make_synopsis_state
from tests.fixtures.redis_fixtures import seed_quiz_state

@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep", "override_db_dependency")
async def test_proceed_marks_ready_and_schedules_background(
    client, 
    fake_cache_store, 
    fake_redis, 
    capture_background_tasks
):
    """
    Verifies that proceeding a quiz:
    1. Opens the 'ready_for_questions' gate in Redis.
    2. Schedules the background agent task to generate baseline questions.
    3. Returns 202 Accepted.
    """
    api = API_PREFIX.rstrip("/")
    quiz_id = uuid.uuid4()

    # 1. Seed Redis with a 'start' state (synopsis ready, gate closed)
    initial_state = make_synopsis_state(quiz_id=quiz_id, category="Dogs")
    # Ensure explicit false for test clarity
    initial_state["ready_for_questions"] = False
    seed_quiz_state(fake_redis, quiz_id, initial_state)

    # 2. Call Proceed
    payload = proceed_payload(quiz_id)
    resp = await client.post(f"{api}/quiz/proceed", json=payload)
    assert resp.status_code == 202, resp.text
    
    body = resp.json()
    assert body["status"] == "processing"
    assert body["quizId"] == str(quiz_id)

    # 3. Verify State Update (Gate Opened)
    key = f"quiz_session:{quiz_id}"
    after_raw = fake_cache_store.get(key)
    assert after_raw
    after = json.loads(after_raw)
    assert after.get("ready_for_questions") is True
    # Baseline shouldn't be ready *immediately* (happens in background task)
    assert after.get("baseline_ready") is False

    # 4. Verify Background Task Scheduled
    assert len(capture_background_tasks) == 1
    func, args, kwargs = capture_background_tasks[0]
    
    assert func is run_agent_in_background
    # The state passed to the task should have the gate open
    task_state = args[0]
    assert task_state["ready_for_questions"] is True
    assert str(task_state["session_id"]) == str(quiz_id)

@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep")
async def test_proceed_404_when_session_missing(client):
    api = API_PREFIX.rstrip("/")
    missing_id = uuid.uuid4()
    
    payload = proceed_payload(missing_id)
    resp = await client.post(f"{api}/quiz/proceed", json=payload)
    
    assert resp.status_code == 404
    assert "not found" in resp.text.lower()