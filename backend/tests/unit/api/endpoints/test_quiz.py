# backend/tests/unit/api/endpoints/test_quiz.py

import asyncio
import json
import time
import uuid
from typing import Any, Dict

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.endpoints.quiz import run_agent_in_background
from app.main import API_PREFIX
from app.models.db import Character, SessionHistory, character_session_map
from app.services.redis_cache import CacheRepository

# Fixtures
from tests.fixtures.agent_graph_fixtures import use_fake_agent_graph  # noqa: F401
from tests.fixtures.background_tasks import capture_background_tasks  # noqa: F401
from tests.fixtures.db_fixtures import override_db_dependency  # noqa: F401
from tests.fixtures.redis_fixtures import (  # noqa: F401
    fake_cache_store,
    fake_redis,
    override_redis_dep,
    seed_quiz_state,
)
from tests.fixtures.turnstile_fixtures import turnstile_bypass  # noqa: F401

# Helpers
from tests.helpers.sample_payloads import (
    next_question_payload,
    proceed_payload,
    start_quiz_payload,
    status_params,
)
from tests.helpers.state_builders import (
    make_finished_state,
    make_questions_state,
    make_synopsis_state,
)

api = API_PREFIX.rstrip("/")
pytestmark = pytest.mark.anyio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _post_start(async_client, category="Cats") -> Any:
    """Helper to POST /quiz/start with standard payload."""
    return await async_client.post(
        f"{api}/quiz/start",
        json=start_quiz_payload(topic=category)
    )

def _parse_redis_state(fake_cache_store, quiz_id: str) -> Dict[str, Any]:
    """Helper to retrieve and parse state from the fake redis store."""
    key = f"quiz_session:{quiz_id}"
    raw = fake_cache_store.get(key)
    assert raw, f"State for {quiz_id} not found in Redis"
    return json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))


# ---------------------------------------------------------------------------
# POST /quiz/start
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep", "turnstile_bypass", "override_db_dependency")
async def test_start_happy_path(async_client, fake_cache_store):
    """
    Verifies that /quiz/start:
    1. Returns 201 Created.
    2. Returns a valid FrontendStartQuizResponse (CamelCase keys).
    3. Persists the initial state (synopsis) to Redis.
    """
    response = await _post_start(async_client, category="Sci-Fi")
    
    assert response.status_code == 201, response.text
    data = response.json()
    
    # Check Response Structure (CamelCase from APIBaseModel)
    assert "quizId" in data
    quiz_id = data["quizId"]
    
    # Check Initial Payload (Synopsis)
    # The union discriminator is 'type'
    initial = data["initialPayload"]
    assert initial["type"] == "synopsis"
    assert initial["data"]["title"]  # Should exist
    assert initial["data"]["summary"] # Should exist

    # Check Characters Payload (FakeGraph generates them in Phase 1)
    chars_payload = data.get("charactersPayload")
    assert chars_payload is not None
    assert chars_payload["type"] == "characters"
    assert len(chars_payload["data"]) >= 3
    assert "shortDescription" in chars_payload["data"][0] # CamelCase check

    # Verify Redis persistence
    stored_state = _parse_redis_state(fake_cache_store, quiz_id)
    assert stored_state["session_id"] == quiz_id
    assert stored_state["category"] == "Sci-Fi"
    assert stored_state["synopsis"]["title"]


@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep", "turnstile_bypass", "override_db_dependency")
async def test_start_db_persistence(async_client, sqlite_db_session: AsyncSession):
    """
    Verifies DB persistence logic:
    1. SessionHistory created.
    2. Characters created (deduplicated).
    3. M:N associations created.
    """
    response = await _post_start(async_client, category="Harry Potter")
    assert response.status_code == 201
    quiz_id = uuid.UUID(response.json()["quizId"])

    # 1. Verify SessionHistory
    stmt = select(SessionHistory).where(SessionHistory.session_id == quiz_id)
    session_row = (await sqlite_db_session.execute(stmt)).scalar_one_or_none()
    assert session_row is not None
    assert session_row.category == "Harry Potter"
    # Check that synopsis dict was stored in JSONB column
    assert session_row.category_synopsis.get("title")

    # 2. Verify Characters (FakeGraph makes "The Optimist", etc.)
    # Count total characters in DB
    char_count = (await sqlite_db_session.execute(select(func.count(Character.id)))).scalar()
    assert char_count >= 3

    # 3. Verify Associations (M:N)
    assoc_stmt = select(func.count()).select_from(character_session_map).where(
        character_session_map.c.session_id == quiz_id
    )
    assoc_count = (await sqlite_db_session.execute(assoc_stmt)).scalar()
    assert assoc_count >= 3


@pytest.mark.usefixtures("override_redis_dep", "turnstile_bypass", "override_db_dependency")
async def test_start_timeout_returns_synopsis_only(async_client, monkeypatch):
    """
    Verifies that if character generation is slow, the endpoint returns 
    just the synopsis immediately to satisfy the strict HTTP timeout budget.
    """
    from app.main import app as fastapi_app
    
    # Mock a slow graph that returns synopsis quickly but hangs on streaming chars
    class SlowStreamGraph:
        async def ainvoke(self, state, config):
            # Phase 1: Synopsis done instantly
            state["synopsis"] = {"title": "Slow Quiz", "summary": "..."}
            state["generated_characters"] = []
            return state

        async def aget_state(self, config):
            # Return snapshot
            class Snap:
                values = {
                    "synopsis": {"title": "Slow Quiz", "summary": "..."},
                    "generated_characters": [],
                    "session_id": uuid.uuid4(),
                    "trace_id": "t-1"
                }
            return Snap()

        async def astream(self, state, config):
            # Simulate slow streaming (longer than our budget)
            await asyncio.sleep(0.2)
            yield {"tick": 1}

    monkeypatch.setattr(fastapi_app.state, "agent_graph", SlowStreamGraph(), raising=False)

    # Mock Settings to have a tiny stream budget
    from app.api.endpoints import quiz as quiz_module
    
    mock_settings = type("Settings", (), {})()
    # Set budget to 0.01s, much less than the 0.2s sleep above
    mock_quiz = type("QuizConfig", (), {"first_step_timeout_s": 5.0, "stream_budget_s": 0.01})()
    mock_app = type("AppConfig", (), {"environment": "test"})()
    
    mock_settings.quiz = mock_quiz
    mock_settings.app = mock_app
    
    monkeypatch.setattr(quiz_module, "settings", mock_settings)

    # Execute
    t0 = time.time()
    response = await _post_start(async_client)
    duration = time.time() - t0

    assert response.status_code == 201
    data = response.json()
    
    # Must contain synopsis
    assert data["initialPayload"]["data"]["title"] == "Slow Quiz"
    # Must NOT contain characters (timed out)
    assert data.get("charactersPayload") is None
    # Must verify we didn't wait the full 0.2s (overhead allowed)
    assert duration < 0.5


@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep", "turnstile_bypass")
async def test_start_503_on_graph_failure(async_client, monkeypatch):
    from app.main import app as fastapi_app

    async def boom(*args, **kwargs):
        raise RuntimeError("Graph Crashed")

    monkeypatch.setattr(fastapi_app.state.agent_graph, "ainvoke", boom, raising=True)
    
    response = await _post_start(async_client)
    assert response.status_code == 503
    assert "unexpected error" in response.json()["detail"]


# ---------------------------------------------------------------------------
# POST /quiz/proceed
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep", "capture_background_tasks")
async def test_proceed_happy_path(async_client, fake_cache_store, fake_redis, capture_background_tasks):
    """
    Verifies /quiz/proceed:
    1. Sets ready_for_questions = True.
    2. Returns 202 Accepted.
    3. Schedules background generation.
    """
    quiz_id = uuid.uuid4()
    # Seed state with synopsis/chars (Phase 1 done)
    state = make_synopsis_state(quiz_id=quiz_id, characters=[{"name": "A"}])
    state["ready_for_questions"] = False
    
    # Fix: Pass fake_redis instance directly, not by calling the fixture
    seed_quiz_state(fake_redis, quiz_id, state)

    response = await async_client.post(f"{api}/quiz/proceed", json=proceed_payload(quiz_id))
    
    assert response.status_code == 202
    data = response.json()
    assert data["status"] == "processing"
    assert data["quizId"] == str(quiz_id)

    # Verify Redis Update
    stored = _parse_redis_state(fake_cache_store, str(quiz_id))
    assert stored["ready_for_questions"] is True

    # Verify Background Task
    assert len(capture_background_tasks) == 1
    func, args, _ = capture_background_tasks[0]
    assert func is run_agent_in_background
    # Task receives updated state
    assert args[0]["ready_for_questions"] is True


@pytest.mark.usefixtures("override_redis_dep")
async def test_proceed_404_missing_session(async_client):
    response = await async_client.post(
        f"{api}/quiz/proceed", 
        json=proceed_payload(uuid.uuid4())
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /quiz/next
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep", "capture_background_tasks", "override_db_dependency")
async def test_next_happy_path_option(async_client, fake_cache_store, fake_redis, capture_background_tasks):
    """
    Verifies answering a question via option index.
    """
    quiz_id = uuid.uuid4()
    # Seed state: 1 question generated, baseline_count=1.
    state = make_questions_state(
        quiz_id=quiz_id, 
        questions=["Q1"], 
        baseline_count=1, 
        answers=[]
    )
    seed_quiz_state(fake_redis, quiz_id, state)

    # Submit Answer: Index 0, Option 1 ("No")
    payload = next_question_payload(quiz_id, index=0, option_idx=1)
    response = await async_client.post(f"{api}/quiz/next", json=payload)

    assert response.status_code == 202
    assert response.json()["status"] == "processing"

    # Verify Redis Update
    stored = _parse_redis_state(fake_cache_store, str(quiz_id))
    history = stored["quiz_history"]
    assert len(history) == 1
    assert history[0]["question_index"] == 0
    assert history[0]["option_index"] == 1
    assert history[0]["answer_text"] == "No"

    # Verify Background Task (since 1 answered >= baseline 1)
    assert len(capture_background_tasks) == 1


@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep", "capture_background_tasks", "override_db_dependency")
async def test_next_happy_path_freeform(async_client, fake_cache_store, fake_redis):
    """
    Verifies answering a question via freeform text.
    """
    quiz_id = uuid.uuid4()
    state = make_questions_state(quiz_id=quiz_id, questions=["Q1"], answers=[])
    seed_quiz_state(fake_redis, quiz_id, state)

    payload = next_question_payload(quiz_id, index=0, freeform="Custom Answer")
    response = await async_client.post(f"{api}/quiz/next", json=payload)

    assert response.status_code == 202
    
    stored = _parse_redis_state(fake_cache_store, str(quiz_id))
    last_ans = stored["quiz_history"][0]
    assert last_ans["answer_text"] == "Custom Answer"
    assert last_ans["option_index"] is None


@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_next_idempotency_duplicate_answer(async_client, fake_cache_store, fake_redis, capture_background_tasks):
    """
    If client re-submits an answer for an index already in history, 
    return 202 but do NOT duplicate history or schedule task.
    """
    quiz_id = uuid.uuid4()
    # State: Q1 already answered (index 0)
    state = make_questions_state(quiz_id=quiz_id, questions=["Q1"], answers=[0])
    seed_quiz_state(fake_redis, quiz_id, state)

    # Re-submit index 0
    payload = next_question_payload(quiz_id, index=0, option_idx=0)
    response = await async_client.post(f"{api}/quiz/next", json=payload)

    assert response.status_code == 202
    
    # History check: should still have only 1 item
    stored = _parse_redis_state(fake_cache_store, str(quiz_id))
    assert len(stored["quiz_history"]) == 1
    
    # Task check: no new task
    assert len(capture_background_tasks) == 0


@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_next_409_out_of_order(async_client, fake_redis):
    """
    Skipping a question (answering index 1 when 0 is pending) returns 409.
    """
    quiz_id = uuid.uuid4()
    state = make_questions_state(quiz_id=quiz_id, questions=["Q0", "Q1"], answers=[])
    seed_quiz_state(fake_redis, quiz_id, state)

    payload = next_question_payload(quiz_id, index=1, option_idx=0)
    response = await async_client.post(f"{api}/quiz/next", json=payload)
    
    assert response.status_code == 409
    assert "out-of-order" in response.json()["detail"].lower()


@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_next_400_index_out_of_range(async_client, fake_redis):
    """Answering a question index that doesn't exist generated yet."""
    quiz_id = uuid.uuid4()
    # FORCE EMPTY QUESTIONS (override default from builder)
    state = make_questions_state(quiz_id=quiz_id, questions=[], answers=[])
    state["generated_questions"] = []
    
    seed_quiz_state(fake_redis, quiz_id, state)

    # Request index 0, but 0 questions exist -> 400
    payload = next_question_payload(quiz_id, index=0, option_idx=0)
    response = await async_client.post(f"{api}/quiz/next", json=payload)
    
    assert response.status_code == 400
    assert "out of range" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# GET /quiz/status/{id}
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("override_redis_dep")
async def test_status_returns_next_question(async_client, fake_redis):
    """
    If the next question is available (and unseen by client), return it.
    """
    quiz_id = uuid.uuid4()
    # 2 Questions generated. Client has answered 0 (aka seen none?). 
    # Logic: target = max(answered_len, known_count). 
    # If answered=0 and known=0, target=0. Return Q0.
    state = make_questions_state(quiz_id=quiz_id, questions=["Q0", "Q1"], answers=[])
    seed_quiz_state(fake_redis, quiz_id, state)

    response = await async_client.get(
        f"{api}/quiz/status/{quiz_id}",
        params=status_params(known_questions_count=0)
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "active"
    assert data["type"] == "question"
    assert data["data"]["text"] == "Q0"


@pytest.mark.usefixtures("override_redis_dep")
async def test_status_processing_when_caught_up(async_client, fake_redis):
    """
    If client has seen all generated questions, return processing status.
    """
    quiz_id = uuid.uuid4()
    # 1 Generated. 1 Answered. Client knows 1.
    # Target = max(1, 1) = 1. generated[1] does not exist.
    state = make_questions_state(quiz_id=quiz_id, questions=["Q0"], answers=[0])
    seed_quiz_state(fake_redis, quiz_id, state)

    response = await async_client.get(
        f"{api}/quiz/status/{quiz_id}",
        params=status_params(known_questions_count=1)
    )
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "processing"


@pytest.mark.usefixtures("override_redis_dep")
async def test_status_returns_result(async_client, fake_redis):
    """
    If final_result is present in state, return it.
    """
    quiz_id = uuid.uuid4()
    state = make_finished_state(
        quiz_id=quiz_id,
        result={"title": "You are Awesome", "description": "Great job."}
    )
    seed_quiz_state(fake_redis, quiz_id, state)

    response = await async_client.get(f"{api}/quiz/status/{quiz_id}")
    
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "finished"
    assert data["type"] == "result"
    assert data["data"]["title"] == "You are Awesome"


@pytest.mark.usefixtures("override_redis_dep")
async def test_status_404(async_client):
    response = await async_client.get(f"{api}/quiz/status/{uuid.uuid4()}")
    assert response.status_code == 404


@pytest.mark.usefixtures("override_redis_dep")
async def test_status_500_malformed_state(async_client, fake_redis):
    """
    If state exists but is missing required keys for the response model (e.g. result title).
    """
    quiz_id = uuid.uuid4()
    state = make_finished_state(quiz_id=quiz_id)
    # Break the result payload
    state["final_result"] = {"description": "Missing Title"} 
    seed_quiz_state(fake_redis, quiz_id, state)

    response = await async_client.get(f"{api}/quiz/status/{quiz_id}")
    assert response.status_code == 500
    assert "malformed" in response.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Expanded Coverage Tests
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures("override_redis_dep", "turnstile_bypass")
async def test_start_500_missing_graph(async_client, monkeypatch):
    """
    Verifies that if the agent graph is missing from app.state (bootstrap failure),
    it returns 500.
    """
    from app.main import app as fastapi_app
    # Simulate missing graph in app.state
    monkeypatch.delattr(fastapi_app.state, "agent_graph", raising=False)

    response = await _post_start(async_client)
    assert response.status_code == 500
    assert "agent service is not available" in response.json()["detail"].lower()


@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep", "turnstile_bypass", "override_db_dependency")
async def test_start_malformed_characters_graceful_degradation(async_client, monkeypatch):
    """
    Verifies that if characters are generated but don't match the schema (validation error),
    the endpoint logs it but still returns the synopsis (omit chars payload).
    """
    from app.main import app as fastapi_app
    
    # Graph returns valid synopsis but malformed character
    class MalformedCharGraph:
        async def ainvoke(self, state, config):
            state["synopsis"] = {"title": "T", "summary": "S"}
            # Missing 'short_description' etc.
            state["generated_characters"] = [{"name": "Missing Fields"}]
            return state
        
        async def aget_state(self, config):
            class Snap:
                values = {
                    "synopsis": {"title": "T", "summary": "S"},
                    "generated_characters": [{"name": "Missing Fields"}],
                    "session_id": uuid.uuid4(),
                    "trace_id": "t-1"
                }
            return Snap()
        
        async def astream(self, state, config):
            yield {}

    monkeypatch.setattr(fastapi_app.state, "agent_graph", MalformedCharGraph(), raising=False)

    response = await _post_start(async_client)
    
    # Should succeed despite bad characters
    assert response.status_code == 201
    data = response.json()
    assert data["initialPayload"]["data"]["title"] == "T"
    # Characters payload should be None because validation failed
    assert data.get("charactersPayload") is None


@pytest.mark.usefixtures("override_redis_dep", "override_db_dependency")
async def test_next_400_option_out_of_range(async_client, fake_redis):
    """
    Verifies 400 if option_index is out of bounds for the specific question.
    """
    quiz_id = uuid.uuid4()
    # Question has 2 options (indexes 0, 1)
    state = make_questions_state(
        quiz_id=quiz_id, 
        questions=[{"text": "Q0", "options": ["Yes", "No"]}], 
        answers=[]
    )
    seed_quiz_state(fake_redis, quiz_id, state)

    # Try accessing index 5
    payload = next_question_payload(quiz_id, index=0, option_idx=5)
    response = await async_client.post(f"{api}/quiz/next", json=payload)

    assert response.status_code == 400
    assert "option_index out of range" in response.json()["detail"].lower()


@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep", "override_db_dependency")
async def test_next_409_atomic_update_failure(async_client, fake_redis, monkeypatch):
    """
    Verifies 409 if Redis atomic update fails (simulating race condition).
    """
    quiz_id = uuid.uuid4()
    state = make_questions_state(quiz_id=quiz_id, questions=["Q1"], answers=[])
    seed_quiz_state(fake_redis, quiz_id, state)

    # Mock CacheRepository.update_quiz_state_atomically to return None (failure)
    async def mock_update_atomically(*args, **kwargs):
        return None
    
    monkeypatch.setattr(CacheRepository, "update_quiz_state_atomically", mock_update_atomically)

    payload = next_question_payload(quiz_id, index=0, option_idx=0)
    response = await async_client.post(f"{api}/quiz/next", json=payload)

    assert response.status_code == 409
    assert "retry answer submission" in response.json()["detail"].lower()


@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep", "override_db_dependency")
async def test_next_db_snapshot_failure_is_non_fatal(async_client, fake_redis, monkeypatch):
    """
    Verifies that if the DB update for QA history fails, the API still returns 202 Accepted.
    """
    quiz_id = uuid.uuid4()
    state = make_questions_state(quiz_id=quiz_id, questions=["Q1"], answers=[])
    seed_quiz_state(fake_redis, quiz_id, state)

    # Mock SessionRepository to raise Exception
    from app.services.database import SessionRepository
    def mock_update_qa_history(*args, **kwargs):
        raise RuntimeError("DB unavailable")
    
    monkeypatch.setattr(SessionRepository, "update_qa_history", mock_update_qa_history)

    payload = next_question_payload(quiz_id, index=0, option_idx=0)
    response = await async_client.post(f"{api}/quiz/next", json=payload)

    # Should still succeed at API level because DB snapshot is try/excepted
    assert response.status_code == 202
    assert response.json()["status"] == "processing"


@pytest.mark.usefixtures("override_redis_dep")
async def test_status_500_malformed_question(async_client, fake_redis, monkeypatch):
    """
    Verifies 500 if a question exists in state but is completely invalid (causing processing error).
    We bypass repository validation by mocking CacheRepository.get_quiz_state to return 
    the malformed state object directly. We purposely use a list item that is NOT a dict 
    to force the response formatter to crash.
    """
    quiz_id = uuid.uuid4()
    # Malformed question: injecting a string instead of a dict/object into the list.
    # This mimics a scenario where data is corrupted or schema mismatch occurs.
    state = make_questions_state(quiz_id=quiz_id, questions=[], answers=[])
    state["generated_questions"] = ["INVALID_DATA_STRUCTURE"] 
    
    # Helper class to mimic Pydantic model's behavior
    class MockModel:
        def model_dump(self):
            return state

    # Mock CacheRepository.get_quiz_state to bypass validation and return our MockModel
    async def mock_get_state(*args, **kwargs):
        return MockModel()

    monkeypatch.setattr(CacheRepository, "get_quiz_state", mock_get_state)

    response = await async_client.get(f"{api}/quiz/status/{quiz_id}")
    
    assert response.status_code == 500
    assert "malformed question data" in response.json()["detail"].lower()