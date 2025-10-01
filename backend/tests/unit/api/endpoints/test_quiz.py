# backend/tests/unit/api/endpoints/test_quiz.py

import json
import uuid
import pytest

from app.main import API_PREFIX
from app.api.endpoints.quiz import run_agent_in_background

# Ensure fixtures are registered
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


# -------------------------------
# Helper for /quiz/start
# -------------------------------

async def _post_start(async_client, *, category="Cats", params=None, token="fake-token"):
    """
    Some environments may still require debug query params (_a/_k).
    Supplying them is harmless if they aren't required.
    """
    q = {"_a": "test-agent", "_k": "test-key"}
    if params:
        q.update(params)
    return await async_client.post(
        f"{api}/quiz/start",
        params=q,
        json={"category": category, "cf-turnstile-response": token},
    )

def _seed_minimal_valid_quiz(fake_redis, qid, **overrides):
    base = {
        "session_id": str(qid),
        "trace_id": "t-1",
        "category": "Cats",
        "messages": [],
        "category_synopsis": {"title": "Quiz: Cats", "summary": "..."},
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

@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep", "turnstile_bypass")
async def test_start_201_happy_path(async_client):
    r = await _post_start(async_client, category="Cats")
    assert r.status_code == 201, r.text
    body = r.json()
    assert "quizId" in body
    assert body["initialPayload"]["type"] == "synopsis"
    # May or may not be present depending on the graph; both are valid
    assert "charactersPayload" in body or body.get("charactersPayload") is None


@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep", "turnstile_bypass")
async def test_start_500_when_agent_graph_missing(async_client):
    # Temporarily remove the agent graph to trigger get_agent_graph failure
    from app.main import app as fastapi_app
    old = getattr(fastapi_app.state, "agent_graph", None)
    fastapi_app.state.agent_graph = None
    try:
        r = await _post_start(async_client, category="Cats")
        assert r.status_code == 500
        assert "Agent service is not available" in r.text
    finally:
        fastapi_app.state.agent_graph = old


@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep", "turnstile_bypass")
async def test_start_503_on_generic_failure(async_client, monkeypatch):
    # Make the initial ainvoke blow up → endpoint wraps as 503
    from app.main import app as fastapi_app

    async def _boom(_state, _config):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(fastapi_app.state.agent_graph, "ainvoke", _boom, raising=True)
    r = await _post_start(async_client, category="Cats")
    assert r.status_code == 503
    assert "unexpected error" in r.text.lower()


@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep", "turnstile_bypass")
async def test_start_returns_synopsis_only_when_no_characters_in_stream(async_client, monkeypatch):
    # Force ainvoke to return synopsis with no characters, and astream to NOT add any
    from app.main import app as fastapi_app

    base_state = {
        "category_synopsis": {"title": "Quiz: X", "summary": "..."},
        "generated_characters": [],
    }

    # Accept any extra kwargs for future-proofing
    async def _ainvoke_no_chars(state, *_a, **_k):
        # Return synopsis + no characters
        return {**state, **base_state}

    # Must be an async generator and accept config=...
    async def _astream_noop(_state, *, config=None, **_k):
        if False:
            yield  # keeps it an async generator without producing items

    class _Snap:
        def __init__(self, values):
            self.values = values

    # Must accept config=...
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

@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep", "turnstile_bypass")
async def test_proceed_202_marks_ready_and_schedules_background(
    async_client, fake_cache_store, capture_background_tasks
):
    # Start → create a real session persisted in cache
    r = await _post_start(async_client, category="Cats")
    assert r.status_code == 201, r.text
    quiz_id = r.json()["quizId"]

    key = f"quiz_session:{quiz_id}"
    before_raw = fake_cache_store.get(key)
    assert before_raw
    before = json.loads(before_raw if isinstance(before_raw, str) else before_raw.decode("utf-8"))
    assert before.get("ready_for_questions") is False

    # Proceed
    pr = await async_client.post(f"{api}/quiz/proceed", json={"quizId": quiz_id})
    assert pr.status_code == 202, pr.text
    body = pr.json()
    assert body["status"] == "processing"
    assert body["quizId"] == quiz_id

    # 1) Gate flipped BEFORE scheduling
    after_raw = fake_cache_store.get(key)
    assert after_raw
    after = json.loads(after_raw if isinstance(after_raw, str) else after_raw.decode("utf-8"))
    assert after.get("ready_for_questions") is True
    assert after.get("baseline_ready") in (False, None)

    # 2) Exactly one background task scheduled
    assert len(capture_background_tasks) == 1
    func, args, kwargs = capture_background_tasks[0]
    assert func is run_agent_in_background
    task_state = args[0]
    assert isinstance(task_state, dict)
    assert str(task_state.get("session_id")) == str(quiz_id)
    assert task_state.get("ready_for_questions") is True
    agent_graph = args[2]
    assert all(hasattr(agent_graph, m) for m in ("ainvoke", "astream", "aget_state"))


@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_proceed_404_when_session_missing(async_client):
    missing_id = str(uuid.uuid4())
    pr = await async_client.post(f"{api}/quiz/proceed", json={"quizId": missing_id})
    assert pr.status_code == 404
    assert "not found" in pr.text.lower()


@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_proceed_twice_schedules_each_time_and_keeps_state(
    async_client, fake_cache_store, capture_background_tasks, fake_redis
):
    # Seed a minimal valid state directly
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

    r1 = await async_client.post(f"{api}/quiz/proceed", json={"quizId": str(quiz_id)})
    assert r1.status_code == 202

    r2 = await async_client.post(f"{api}/quiz/proceed", json={"quizId": str(quiz_id)})
    assert r2.status_code == 202

    assert len(capture_background_tasks) == 2
    assert all(call[0] is run_agent_in_background for call in capture_background_tasks)

    key = f"quiz_session:{quiz_id}"
    raw = fake_cache_store.get(key)
    assert raw
    doc = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
    assert doc.get("ready_for_questions") is True
    assert doc.get("baseline_ready") is False
    assert int(doc.get("baseline_count") or 0) == 0


# -------------------------------
# /quiz/next
# -------------------------------

@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_next_404_when_session_missing(async_client):
    missing = str(uuid.uuid4())
    r = await async_client.post(
        f"{api}/quiz/next",
        json={"quizId": missing, "questionIndex": 0, "optionIndex": 0},
    )
    assert r.status_code == 404


@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_next_400_when_option_index_out_of_range(async_client, fake_redis):
    qid = uuid.uuid4()
    _seed_minimal_valid_quiz(
        fake_redis, qid,
        generated_questions=[{"question_text": "Q1", "options": [{"text": "A"}, {"text": "B"}]}],
        quiz_history=[],
        baseline_count=1,
        baseline_ready=True,
        ready_for_questions=True,
    )

    r = await async_client.post(
        f"{api}/quiz/next",
        json={"quizId": str(qid), "questionIndex": 0, "optionIndex": 99},  # <-- camelCase
    )

    assert r.status_code == 400
    # depending on the endpoint's message casing:
    assert "out of range" in r.text.lower()


@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_next_202_when_question_index_negative_treated_as_duplicate(async_client, fake_redis, capture_background_tasks):
    qid = uuid.uuid4()
    _seed_minimal_valid_quiz(fake_redis, qid,
        generated_questions=[{"question_text": "Q1", "options": [{"text": "A"}, {"text": "B"}]}],
        quiz_history=[],
        baseline_count=1,
        baseline_ready=True,
        ready_for_questions=True,
    )
    r = await async_client.post(
        f"{api}/quiz/next",
        json={"quizId": str(qid), "questionIndex": -1, "optionIndex": 0},
    )
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "processing"
    assert body["quizId"] == str(qid)
    assert capture_background_tasks == []  # duplicates shouldn't schedule


@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_next_400_when_question_index_beyond_questions(async_client, fake_redis):
    qid = uuid.uuid4()
    _seed_minimal_valid_quiz(fake_redis, qid,
        generated_questions=[{"question_text": "Q1", "options": [{"text": "A"}, {"text": "B"}]}],
        # One question total, but mark question 0 as already answered → expected_index = 1
        quiz_history=[{"question_index": 0, "question_text": "Q1", "answer_text": "A", "option_index": 0}],
        baseline_count=1,
        baseline_ready=True,
        ready_for_questions=True,
    )
    r = await async_client.post(
        f"{api}/quiz/next",
        json={"quizId": str(qid), "questionIndex": 1, "optionIndex": 0},
    )
    assert r.status_code == 400
    assert "out of range" in r.text.lower()


@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_next_409_out_of_order_and_202_duplicate(async_client, fake_redis, capture_background_tasks):
    qid = uuid.uuid4()
    _seed_minimal_valid_quiz(fake_redis, qid,
        generated_questions=[{"question_text": "Q1", "options": [{"text": "A"}, {"text": "B"}]}],
        quiz_history=[{"question_index": 0, "question_text": "Q1", "answer_text": "A", "option_index": 0}],
        baseline_count=1,
        baseline_ready=True,
        ready_for_questions=True,
    )

    # Out-of-order (skipping ahead)
    r1 = await async_client.post(
        f"{api}/quiz/next",
        json={"quizId": str(qid), "questionIndex": 2, "optionIndex": 0},
    )
    assert r1.status_code == 409

    # Duplicate → 202; should NOT schedule background
    r2 = await async_client.post(
        f"{api}/quiz/next",
        json={"quizId": str(qid), "questionIndex": 0, "optionIndex": 0},
    )
    assert r2.status_code == 202
    assert capture_background_tasks == []


@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_next_409_when_atomic_update_conflicts(async_client, fake_redis, monkeypatch):
    # Force CacheRepository.update_quiz_state_atomically to return None
    from app.api.endpoints import quiz as quiz_mod

    qid = uuid.uuid4()
    _seed_minimal_valid_quiz(fake_redis, qid,
        generated_questions=[{"question_text": "Q1", "options": [{"text": "A"}, {"text": "B"}]}],
        quiz_history=[],
        baseline_count=1,
        baseline_ready=True,
        ready_for_questions=True,
    )

    async def _return_none(_self, *_a, **_k):
        return None

    monkeypatch.setattr(quiz_mod.CacheRepository, "update_quiz_state_atomically", _return_none, raising=True)
    r = await async_client.post(
        f"{api}/quiz/next",
        json={"quizId": str(qid), "questionIndex": 0, "optionIndex": 0},
    )
    assert r.status_code == 409
    assert "retry" in r.text.lower() or "conflict" in r.text.lower()


@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_next_accepts_free_text_answer(async_client, fake_redis, fake_cache_store):
    qid = uuid.uuid4()
    _seed_minimal_valid_quiz(fake_redis, qid,
        generated_questions=[{"question_text": "Q1", "options": [{"text": "A"}, {"text": "B"}]}],
        quiz_history=[],
        baseline_count=1,
        baseline_ready=True,
        ready_for_questions=True,
    )
    r = await async_client.post(
        f"{api}/quiz/next",
        json={"quizId": str(qid), "questionIndex": 0, "answer": "custom text"},
    )
    assert r.status_code == 202
    # Verify stored answer text
    key = f"quiz_session:{qid}"
    raw = fake_cache_store.get(key)
    doc = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
    assert doc["quiz_history"][-1]["answer_text"] == "custom text"


# -------------------------------
# /quiz/status
# -------------------------------

@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep")
async def test_status_404_missing(async_client):
    r = await async_client.get(f"{api}/quiz/status/{uuid.uuid4()}")
    assert r.status_code == 404


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep")
async def test_status_processing_when_no_new_questions(async_client, fake_redis):
    qid = uuid.uuid4()
    _seed_minimal_valid_quiz(fake_redis, qid,
        generated_questions=[{"question_text": "Q1", "options": [{"text": "A"}, {"text": "B"}]}],
        quiz_history=[{"question_index": 0, "question_text": "Q1", "answer_text": "A", "option_index": 0}],
        baseline_count=1,
        baseline_ready=True,
        ready_for_questions=True,
    )
    r = await async_client.get(f"{api}/quiz/status/{qid}?known_questions_count=1")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "processing"


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep")
async def test_status_returns_next_unseen_question(async_client, fake_redis):
    qid = uuid.uuid4()
    qs = [
        {"question_text": "Q1", "options": [{"text": "A"}, {"text": "B"}]},
        {"question_text": "Q2", "options": [{"text": "C"}, {"text": "D"}]},
    ]
    _seed_minimal_valid_quiz(fake_redis, qid,
        generated_questions=qs,
        quiz_history=[{"question_index": 0, "question_text": "Q1", "answer_text": "A", "option_index": 0}],
        baseline_count=2,
        baseline_ready=True,
        ready_for_questions=True,
    )
    # Client has seen only 1 question; next unseen should be Q2 (index=1)
    r = await async_client.get(f"{api}/quiz/status/{qid}?known_questions_count=1")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "active"
    assert body["type"] == "question"
    assert body["data"]["text"] == "Q2"


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep")
async def test_status_500_malformed_final_result(async_client, fake_redis):
    qid = uuid.uuid4()
    # Must be a dict so Redis deserialization passes; shape is wrong so the endpoint will 500.
    _seed_minimal_valid_quiz(fake_redis, qid, final_result={"bogus": True})
    r = await async_client.get(f"{api}/quiz/status/{qid}")
    assert r.status_code == 500
    assert "malformed result" in r.text.lower()


@pytest.mark.anyio
@pytest.mark.usefixtures("override_redis_dep")
async def test_status_500_malformed_question(async_client, fake_redis):
    qid = uuid.uuid4()
    # Int is JSON-serializable but invalid for QuizQuestion.model_validate in /quiz/status
    _seed_minimal_valid_quiz(fake_redis, qid,
        generated_questions=[12345],
        quiz_history=[],
        baseline_count=1,
        baseline_ready=True,
        ready_for_questions=True,
    )
    r = await async_client.get(f"{api}/quiz/status/{qid}")
    # With strict Redis deserialization, this is caught earlier and treated as "session not found".
    assert r.status_code == 404
    assert "not found" in r.text.lower()
