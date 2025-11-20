# backend/tests/unit/api/endpoints/test_quiz.py

import json
import uuid
import pytest
from typing import Any, Dict, Optional

from app.main import API_PREFIX
from app.api.endpoints.quiz import run_agent_in_background

# Ensure fixtures are registered (even if not used directly in test args, they modify global state)
from tests.fixtures.turnstile_fixtures import turnstile_bypass  # noqa: F401
from tests.fixtures.agent_graph_fixtures import use_fake_agent_graph  # noqa: F401
from tests.fixtures.redis_fixtures import (  # noqa: F401
    override_redis_dep,
    fake_cache_store,
    fake_redis,
    seed_quiz_state,
)
from tests.fixtures.background_tasks import capture_background_tasks  # noqa: F401


api = API_PREFIX.rstrip("/")
pytestmark = pytest.mark.anyio


# -------------------------------
# Helper for /quiz/start
# -------------------------------

async def _post_start(async_client, *, category="Cats", params=None, token="fake-token"):
    """
    Helper to POST /quiz/start with consistent params/json.
    """
    q = {}
    if params:
        q.update(params)
    return await async_client.post(
        f"{api}/quiz/start",
        params=q,
        json={"category": category, "cf-turnstile-response": token},
    )

def _seed_minimal_valid_quiz(fake_redis, qid: uuid.UUID, **overrides: Any) -> None:
    """
    Seeds Redis with a valid AgentGraphStateModel JSON blob.
    """
    base: Dict[str, Any] = {
        "session_id": str(qid),
        "trace_id": "t-1",
        "category": "Cats",
        "messages": [],
        # Updated key: synopsis (was category_synopsis)
        "synopsis": {"title": "Quiz: Cats", "summary": "..."},
        "agent_plan": {"title": "Quiz: Cats", "synopsis": "...", "ideal_archetypes": []},
        "generated_characters": [],
        "generated_questions": [],
        "quiz_history": [],
        "baseline_count": 0,
        "baseline_ready": False,
        "ready_for_questions": False,
        "is_error": False,
        "error_message": None,
        "error_count": 0,
        "final_result": None,
        "last_served_index": None,
    }
    base.update(overrides)
    seed_quiz_state(fake_redis, qid, base)
    

# -------------------------------
# /quiz/start
# -------------------------------

@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep", "turnstile_bypass")
async def test_start_201_happy_path(async_client):
    """
    Verify successful start returns 201 and correct payload structure.
    """
    r = await _post_start(async_client, category="Cats")
    assert r.status_code == 201, r.text
    body = r.json()
    assert "quizId" in body
    # The fake graph returns a synopsis
    assert body["initialPayload"]["type"] == "synopsis"
    # Characters payload may or may not be present depending on streaming budget/timing
    # But it should be a valid key if present
    if body.get("charactersPayload"):
        assert body["charactersPayload"]["type"] == "characters"
        assert isinstance(body["charactersPayload"]["data"], list)


@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep", "turnstile_bypass")
async def test_start_500_when_agent_graph_missing(async_client):
    """
    Verify 500 error when agent service is unavailable (app state missing graph).
    """
    from app.main import app as fastapi_app
    
    # Temporarily remove the agent graph
    old = getattr(fastapi_app.state, "agent_graph", None)
    fastapi_app.state.agent_graph = None
    
    try:
        r = await _post_start(async_client, category="Cats")
        assert r.status_code == 500
        assert "Agent service is not available" in r.text
    finally:
        # Restore it
        fastapi_app.state.agent_graph = old


@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep", "turnstile_bypass")
async def test_start_503_on_generic_failure(async_client, monkeypatch):
    """
    Verify 503 error when the agent graph execution explodes.
    """
    from app.main import app as fastapi_app

    # Mock ainvoke to raise exception
    async def _boom(_state, _config):
        raise RuntimeError("kaboom")

    # We patch the instance on app.state.agent_graph
    # Note: use_fake_agent_graph runs before this, setting app.state.agent_graph to FakeAgentGraph
    monkeypatch.setattr(fastapi_app.state.agent_graph, "ainvoke", _boom, raising=True)
    
    r = await _post_start(async_client, category="Cats")
    assert r.status_code == 503
    assert "unexpected error" in r.text.lower()


@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep", "turnstile_bypass")
async def test_start_returns_synopsis_only_when_no_characters_in_stream(async_client, monkeypatch):
    """
    Verify start returns only synopsis if character generation doesn't complete in time/stream.
    """
    from app.main import app as fastapi_app

    base_state = {
        "synopsis": {"title": "Quiz: X", "summary": "..."},
        "generated_characters": [], # Empty characters
        # Ensure defaults for other required fields
        "session_id": uuid.uuid4(),
        "trace_id": "t",
        "category": "Cats",
    }

    # Mock ainvoke to return state without characters
    async def _ainvoke_no_chars(state, *_a, **_k):
        return {**state, **base_state}

    # Mock astream to yield nothing (empty generator)
    async def _astream_noop(_state, *, config=None, **_k):
        if False: yield

    # Mock aget_state to return the same no-char state
    class _Snap:
        def __init__(self, values): self.values = values
        
    async def _aget_state_stub(*, config=None, **_k):
        return _Snap({**base_state})

    monkeypatch.setattr(fastapi_app.state.agent_graph, "ainvoke", _ainvoke_no_chars, raising=True)
    monkeypatch.setattr(fastapi_app.state.agent_graph, "astream", _astream_noop, raising=True)
    monkeypatch.setattr(fastapi_app.state.agent_graph, "aget_state", _aget_state_stub, raising=True)

    r = await _post_start(async_client, category="Cats")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["initialPayload"]["type"] == "synopsis"
    assert body.get("charactersPayload") is None


# -------------------------------
# /quiz/proceed
# -------------------------------

@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep", "turnstile_bypass")
async def test_proceed_202_marks_ready_and_schedules_background(
    async_client, fake_cache_store, capture_background_tasks
):
    """
    Verify proceed flips the 'ready_for_questions' flag in Redis and schedules a background task.
    """
    # 1. Start session
    r = await _post_start(async_client, category="Cats")
    assert r.status_code == 201
    quiz_id = r.json()["quizId"]

    # Verify initial state in Redis
    key = f"quiz_session:{quiz_id}"
    before_raw = fake_cache_store.get(key)
    assert before_raw
    before = json.loads(before_raw if isinstance(before_raw, str) else before_raw.decode("utf-8"))
    assert before.get("ready_for_questions") is False

    # 2. Proceed
    pr = await async_client.post(f"{api}/quiz/proceed", json={"quizId": quiz_id})
    assert pr.status_code == 202, pr.text
    body = pr.json()
    assert body["status"] == "processing"

    # 3. Verify state updated in Redis (gate opened)
    after_raw = fake_cache_store.get(key)
    assert after_raw
    after = json.loads(after_raw if isinstance(after_raw, str) else after_raw.decode("utf-8"))
    assert after.get("ready_for_questions") is True
    
    # 4. Verify background task scheduled
    assert len(capture_background_tasks) == 1
    func, args, kwargs = capture_background_tasks[0]
    assert func is run_agent_in_background
    task_state = args[0]
    assert str(task_state.get("session_id")) == str(quiz_id)
    assert task_state.get("ready_for_questions") is True


@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_proceed_404_when_session_missing(async_client):
    missing_id = str(uuid.uuid4())
    pr = await async_client.post(f"{api}/quiz/proceed", json={"quizId": missing_id})
    assert pr.status_code == 404


@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_proceed_twice_schedules_each_time_and_keeps_state(
    async_client, fake_cache_store, capture_background_tasks, fake_redis
):
    """
    Verify proceed is idempotent on state but schedules tasks each time (at-least-once behavior).
    """
    quiz_id = uuid.uuid4()
    _seed_minimal_valid_quiz(fake_redis, quiz_id)

    # Call 1
    r1 = await async_client.post(f"{api}/quiz/proceed", json={"quizId": str(quiz_id)})
    assert r1.status_code == 202

    # Call 2
    r2 = await async_client.post(f"{api}/quiz/proceed", json={"quizId": str(quiz_id)})
    assert r2.status_code == 202

    assert len(capture_background_tasks) == 2


# -------------------------------
# /quiz/next
# -------------------------------

@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_next_404_when_session_missing(async_client):
    missing = str(uuid.uuid4())
    r = await async_client.post(
        f"{api}/quiz/next",
        json={"quizId": missing, "questionIndex": 0, "optionIndex": 0},
    )
    assert r.status_code == 404


@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_next_400_when_option_index_out_of_range(async_client, fake_redis):
    qid = uuid.uuid4()
    # Seed with 1 question having 2 options (indices 0, 1)
    _seed_minimal_valid_quiz(
        fake_redis, qid,
        generated_questions=[{"question_text": "Q1", "options": [{"text": "A"}, {"text": "B"}]}],
        baseline_count=1,
        baseline_ready=True,
        ready_for_questions=True,
    )

    r = await async_client.post(
        f"{api}/quiz/next",
        json={"quizId": str(qid), "questionIndex": 0, "optionIndex": 99},
    )
    assert r.status_code == 400
    assert "out of range" in r.text.lower()


@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_next_202_when_question_index_duplicate(async_client, fake_redis, capture_background_tasks):
    """
    Duplicate answer (index < expected) returns 202 but does not schedule background work.
    """
    qid = uuid.uuid4()
    # Q0 already answered
    _seed_minimal_valid_quiz(fake_redis, qid,
        generated_questions=[{"question_text": "Q1", "options": [{"text": "A"}, {"text": "B"}]}],
        quiz_history=[{"question_index": 0, "question_text": "Q1", "answer_text": "A", "option_index": 0}],
        baseline_count=1,
        baseline_ready=True,
        ready_for_questions=True,
    )
    
    # Resubmit Q0
    r = await async_client.post(
        f"{api}/quiz/next",
        json={"quizId": str(qid), "questionIndex": 0, "optionIndex": 0},
    )
    assert r.status_code == 202
    assert capture_background_tasks == []


@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_next_409_out_of_order(async_client, fake_redis):
    """
    Skipping a question returns 409 Conflict.
    """
    qid = uuid.uuid4()
    # No questions answered yet (expected index 0)
    _seed_minimal_valid_quiz(fake_redis, qid,
        generated_questions=[{"question_text": "Q1", "options": []}, {"question_text": "Q2", "options": []}],
        baseline_count=2,
        baseline_ready=True,
        ready_for_questions=True,
    )

    # Try to answer Q1 (index 1)
    r = await async_client.post(
        f"{api}/quiz/next",
        json={"quizId": str(qid), "questionIndex": 1, "optionIndex": 0},
    )
    assert r.status_code == 409
    assert "out-of-order" in r.text.lower()


@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_next_accepts_free_text_answer(async_client, fake_redis, fake_cache_store):
    qid = uuid.uuid4()
    _seed_minimal_valid_quiz(fake_redis, qid,
        generated_questions=[{"question_text": "Q1", "options": [{"text": "A"}, {"text": "B"}]}],
        baseline_count=1,
        baseline_ready=True,
        ready_for_questions=True,
    )

    r = await async_client.post(
        f"{api}/quiz/next",
        json={"quizId": str(qid), "questionIndex": 0, "answer": "my custom answer"},
    )
    assert r.status_code == 202

    # Verify storage
    key = f"quiz_session:{qid}"
    raw = fake_cache_store.get(key)
    doc = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
    last_ans = doc["quiz_history"][-1]
    assert last_ans["answer_text"] == "my custom answer"
    assert last_ans["option_index"] is None


# -------------------------------
# /quiz/status
# -------------------------------

@pytest.mark.usefixtures("override_redis_dep")
async def test_status_404_missing(async_client):
    r = await async_client.get(f"{api}/quiz/status/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.usefixtures("override_redis_dep")
async def test_status_processing_when_no_new_questions(async_client, fake_redis):
    qid = uuid.uuid4()
    # 1 question generated, 1 answered -> client caught up
    _seed_minimal_valid_quiz(fake_redis, qid,
        generated_questions=[{"question_text": "Q1", "options": [{"text": "A"}, {"text": "B"}]}],
        quiz_history=[{"question_index": 0}],
        baseline_count=1,
        baseline_ready=True,
    )
    # Client claims to know 1 question
    r = await async_client.get(f"{api}/quiz/status/{qid}?known_questions_count=1")
    assert r.status_code == 200
    assert r.json()["status"] == "processing"


@pytest.mark.usefixtures("override_redis_dep")
async def test_status_returns_next_unseen_question(async_client, fake_redis):
    qid = uuid.uuid4()
    qs = [
        {"question_text": "Q1", "options": [{"text": "A"}, {"text": "B"}]},
        {"question_text": "Q2", "options": [{"text": "C"}, {"text": "D"}]},
    ]
    # 2 generated, 1 answered. Client knows 1. Next is index 1 (Q2).
    _seed_minimal_valid_quiz(fake_redis, qid,
        generated_questions=qs,
        quiz_history=[{"question_index": 0}],
        baseline_count=2,
        baseline_ready=True,
    )

    r = await async_client.get(f"{api}/quiz/status/{qid}?known_questions_count=1")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "active"
    assert body["type"] == "question"
    assert body["data"]["text"] == "Q2"


@pytest.mark.usefixtures("override_redis_dep")
async def test_status_returns_final_result_when_present(async_client, fake_redis):
    qid = uuid.uuid4()
    _seed_minimal_valid_quiz(fake_redis, qid,
        final_result={"title": "You Won", "description": "Good job", "image_url": None}
    )
    r = await async_client.get(f"{api}/quiz/status/{qid}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "finished"
    assert body["type"] == "result"
    assert body["data"]["title"] == "You Won"


@pytest.mark.usefixtures("override_redis_dep")
async def test_status_500_malformed_final_result(async_client, fake_redis):
    """Endpoint validates schemas; invalid stored result triggers 500."""
    qid = uuid.uuid4()
    # Missing required 'title'
    _seed_minimal_valid_quiz(fake_redis, qid, final_result={"bogus": True})
    r = await async_client.get(f"{api}/quiz/status/{qid}")
    assert r.status_code == 500
    assert "malformed result" in r.text.lower()