import json
import uuid
import pytest

from app.main import API_PREFIX
from app.api.endpoints.quiz import run_agent_in_background
from tests.helpers.sample_payloads import next_question_payload
from tests.helpers.state_builders import make_questions_state
from tests.fixtures.redis_fixtures import seed_quiz_state

@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep", "override_db_dependency")
async def test_next_records_answer_and_returns_processing(
    client, 
    fake_redis, 
    fake_cache_store,
    capture_background_tasks
):
    """
    Verifies submitting a valid answer:
    1. Updates 'quiz_history' in Redis.
    2. Returns 202 Processing.
    3. Does NOT schedule background task if baseline count not yet reached.
    """
    api = API_PREFIX.rstrip("/")
    quiz_id = uuid.uuid4()
    
    # Setup: 3 generated questions, 0 answered. Baseline count 3.
    state = make_questions_state(
        quiz_id=quiz_id,
        category="History",
        questions=["Q1", "Q2", "Q3"],
        baseline_count=3,
        answers=[] 
    )
    seed_quiz_state(fake_redis, quiz_id, state)

    # 1. Submit Answer for Q1 (Index 0)
    payload = next_question_payload(quiz_id, index=0, option_idx=1)
    resp = await client.post(f"{api}/quiz/next", json=payload)
    assert resp.status_code == 202
    assert resp.json()["status"] == "processing"

    # 2. Verify Redis Update
    key = f"quiz_session:{quiz_id}"
    cached = json.loads(fake_cache_store.get(key))
    history = cached.get("quiz_history", [])
    assert len(history) == 1
    assert history[0]["question_index"] == 0
    assert history[0]["option_index"] == 1
    
    # 3. Verify NO background task (answered 1 < baseline 3)
    assert len(capture_background_tasks) == 0

@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep", "override_db_dependency")
async def test_next_schedules_background_after_baseline(
    client, 
    fake_redis, 
    capture_background_tasks
):
    """
    Verifies submitting the LAST baseline answer:
    1. Updates history.
    2. Schedules background agent to generate adaptive questions.
    """
    api = API_PREFIX.rstrip("/")
    quiz_id = uuid.uuid4()

    # Setup: 1 question total, 0 answered. Baseline count 1.
    state = make_questions_state(
        quiz_id=quiz_id,
        questions=["Only Q"],
        baseline_count=1,
        answers=[] 
    )
    seed_quiz_state(fake_redis, quiz_id, state)

    payload = next_question_payload(quiz_id, index=0, option_idx=0)
    resp = await client.post(f"{api}/quiz/next", json=payload)
    assert resp.status_code == 202

    # Verify background task scheduled
    assert len(capture_background_tasks) == 1
    func, args, _ = capture_background_tasks[0]
    assert func is run_agent_in_background
    
    # Task receives updated state
    task_state = args[0]
    assert len(task_state["quiz_history"]) == 1

@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_next_409_when_skipping_ahead(client, fake_redis):
    """
    Verifies that skipping questions returns 409 Conflict.
    This prevents clients from answering out-of-order.
    """
    api = API_PREFIX.rstrip("/")
    quiz_id = uuid.uuid4()
    
    # Setup: 3 Questions exist. History is empty. Next expected index = 0.
    state = make_questions_state(quiz_id=quiz_id, questions=["Q1", "Q2", "Q3"])
    seed_quiz_state(fake_redis, quiz_id, state)

    # Try to answer Index 2 (Skipping 0 and 1)
    # 2 > 0 => 409
    payload = next_question_payload(quiz_id, index=2, option_idx=0)
    resp = await client.post(f"{api}/quiz/next", json=payload)
    assert resp.status_code == 409
    assert "out-of-order" in resp.text.lower()

@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_next_400_when_index_out_of_range(client, fake_redis):
    """
    Verifies that requesting an index that doesn't exist (but is next in order) returns 400.
    """
    api = API_PREFIX.rstrip("/")
    quiz_id = uuid.uuid4()
    
    # Setup: 1 Question exists, and it is already answered.
    # History length = 1. Next expected index = 1.
    # Questions length = 1. Index 1 does not exist in questions list.
    state = make_questions_state(
        quiz_id=quiz_id, 
        questions=["Q1"],
        answers=[0] # Answered index 0
    )
    seed_quiz_state(fake_redis, quiz_id, state)

    # Try to answer Index 1 (Next expected, but doesn't exist)
    # 1 == 1 (Passes 409 out-of-order check)
    # 1 >= len(questions) (Fails range check => 400)
    payload = next_question_payload(quiz_id, index=1, option_idx=0)
    resp = await client.post(f"{api}/quiz/next", json=payload)
    assert resp.status_code == 400
    assert "out of range" in resp.text.lower()