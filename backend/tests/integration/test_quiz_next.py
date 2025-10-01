# backend/tests/integration/test_quiz_next.py

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


api = API_PREFIX.rstrip("/")


# -------------------------------
# Helpers
# -------------------------------

def _load_cached_state(fake_cache_store, quiz_id):
    raw = fake_cache_store.get(f"quiz_session:{quiz_id}")
    assert raw, "Expected state to be saved in cache"
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw)


def _valid_state(session_id, **overrides):
    """
    Produce a state dict that passes AgentGraphStateModel validation.
    Allows tests to override/extend fields without retyping required bits.
    """
    base = {
        "session_id": str(session_id),
        "trace_id": overrides.pop("trace_id", "t-test"),
        "category": overrides.pop("category", "Cats"),
        "messages": overrides.pop("messages", []),
        # common error flags expected by the model
        "is_error": False,
        "error_message": None,
        "error_count": 0,
        # common router flags & defaults that many tests rely on
        "generated_questions": [],
        "quiz_history": [],
        "baseline_count": 0,
        "baseline_ready": False,
        "ready_for_questions": True,
        "last_served_index": -1,
        # a few optional fields the graph may set later
        "ideal_archetypes": [],
        "generated_characters": [],
        "rag_context": [],
        "final_result": None,
    }
    base.update(overrides)
    return base


# -------------------------------
# Basic error cases
# -------------------------------

@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_next_404_when_session_missing(async_client):
    missing = str(uuid.uuid4())
    r = await async_client.post(
        f"{api}/quiz/next",
        json={"quiz_id": missing, "question_index": 0, "option_index": 0},
    )
    assert r.status_code == 404
    assert "not found" in r.text.lower()


@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_next_202_when_question_index_negative_treated_as_duplicate(async_client, fake_redis):
    qid = uuid.uuid4()
    seed_quiz_state(
        fake_redis,
        qid,
        _valid_state(
            qid,
            trace_id="t-neg-idx",
            generated_questions=[{"question_text": "Q1", "options": [{"text": "A"}, {"text": "B"}]}],
            baseline_count=1,
            baseline_ready=True,
            ready_for_questions=True,
        ),
    )

    # Negative index is treated by the endpoint as a duplicate -> 202
    r1 = await async_client.post(
        f"{api}/quiz/next",
        json={"quiz_id": str(qid), "question_index": -1, "option_index": 0},
    )
    assert r1.status_code == 202
    body = r1.json()
    assert body["status"] == "processing"
    assert body["quizId"] == str(qid)


@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_next_400_when_question_index_beyond_questions(async_client, fake_redis):
    qid = uuid.uuid4()
    seed_quiz_state(
        fake_redis,
        qid,
        _valid_state(
            qid,
            trace_id="t-out-of-range",
            generated_questions=[{"question_text": "Q1", "options": [{"text": "A"}, {"text": "B"}]}],
            # One question total, but mark question 0 as already answered
            quiz_history=[{"question_index": 0, "question_text": "Q1", "answer_text": "A", "option_index": 0}],
            baseline_count=1,
            baseline_ready=True,
            ready_for_questions=True,
        ),
    )

    # expected_index == 1 but there is no question at index 1 -> true out-of-range
    r = await async_client.post(
        f"{api}/quiz/next",
        json={"quiz_id": str(qid), "question_index": 1, "option_index": 0},
    )
    assert r.status_code == 400
    assert "out of range" in r.text.lower()


@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_next_400_when_option_index_out_of_range(async_client, fake_redis):
    qid = uuid.uuid4()
    seed_quiz_state(
        fake_redis,
        qid,
        _valid_state(
            qid,
            trace_id="t-opt-oob",
            generated_questions=[{"question_text": "Q1", "options": [{"text": "A"}, {"text": "B"}]}],
            baseline_count=1,
            baseline_ready=True,
            ready_for_questions=True,
        ),
    )
    r = await async_client.post(
        f"{api}/quiz/next",
        json={"quiz_id": str(qid), "question_index": 0, "option_index": 99},
    )
    assert r.status_code == 400
    assert "out of range" in r.text.lower()


# -------------------------------
# Idempotency & ordering
# -------------------------------

@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_next_202_for_duplicate_answer_and_no_background(
    async_client, fake_redis, fake_cache_store, capture_background_tasks
):
    qid = uuid.uuid4()
    # One question, already answered -> duplicate submit should be treated as success
    seed_quiz_state(
        fake_redis,
        qid,
        _valid_state(
            qid,
            trace_id="t-dup",
            generated_questions=[{"question_text": "Q1", "options": [{"text": "A"}, {"text": "B"}]}],
            quiz_history=[{"question_index": 0, "question_text": "Q1", "answer_text": "A", "option_index": 0}],
            baseline_count=1,
            baseline_ready=True,
            ready_for_questions=True,
        ),
    )

    r = await async_client.post(
        f"{api}/quiz/next",
        json={"quiz_id": str(qid), "question_index": 0, "option_index": 0},
    )
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "processing"
    assert body["quizId"] == str(qid)

    # No task scheduled for duplicate
    assert capture_background_tasks == []

    # History length unchanged
    cached = _load_cached_state(fake_cache_store, qid)
    assert len(cached.get("quiz_history") or []) == 1


@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_next_409_when_skipping_ahead(async_client, fake_redis):
    qid = uuid.uuid4()
    seed_quiz_state(
        fake_redis,
        qid,
        _valid_state(
            qid,
            trace_id="t-skip",
            generated_questions=[
                {"question_text": "Q0", "options": [{"text": "A"}, {"text": "B"}]},
                {"question_text": "Q1", "options": [{"text": "C"}, {"text": "D"}]},
            ],
            quiz_history=[],  # expected_index = 0
            baseline_count=2,
            baseline_ready=True,
            ready_for_questions=True,
        ),
    )
    r = await async_client.post(
        f"{api}/quiz/next",
        json={"quiz_id": str(qid), "question_index": 1, "option_index": 0},
    )
    assert r.status_code == 409
    assert "out-of-order" in r.text.lower() or "stale" in r.text.lower() or "conflict" in r.text.lower()


# -------------------------------
# Happy paths & persistence
# -------------------------------

@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_next_accepts_free_text_and_updates_state(
    async_client, fake_redis, fake_cache_store
):
    qid = uuid.uuid4()
    seed_quiz_state(
        fake_redis,
        qid,
        _valid_state(
            qid,
            trace_id="t-free",
            generated_questions=[{"question_text": "Q1", "options": [{"text": "A"}, {"text": "B"}]}],
            quiz_history=[],
            baseline_count=1,
            baseline_ready=True,
            ready_for_questions=True,
        ),
    )

    payload = {"quiz_id": str(qid), "question_index": 0, "answer": "custom text"}
    r = await async_client.post(f"{api}/quiz/next", json=payload)
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "processing"
    assert body["quizId"] == str(qid)

    # Verify snapshot persisted with free-text answer
    cached = _load_cached_state(fake_cache_store, qid)
    hist = cached.get("quiz_history") or []
    assert len(hist) == 1
    assert hist[-1]["answer_text"] == "custom text"
    assert hist[-1]["question_index"] == 0
    # The gate should be open after submit
    assert cached.get("ready_for_questions") is True


@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_next_option_index_overrides_free_text(
    async_client, fake_redis, fake_cache_store
):
    qid = uuid.uuid4()
    seed_quiz_state(
        fake_redis,
        qid,
        _valid_state(
            qid,
            trace_id="t-opt-overrides",
            generated_questions=[
                {"question_text": "Q1", "options": [{"text": "Alpha"}, {"text": "Bravo"}]},
            ],
            quiz_history=[],
            baseline_count=1,
            baseline_ready=True,
            ready_for_questions=True,
        ),
    )

    # Provide both answer and option_index → option text should win
    r = await async_client.post(
        f"{api}/quiz/next",
        json={"quiz_id": str(qid), "question_index": 0, "option_index": 1, "answer": "ignored"},
    )
    assert r.status_code == 202

    cached = _load_cached_state(fake_cache_store, qid)
    hist = cached.get("quiz_history") or []
    assert len(hist) == 1
    assert hist[-1]["answer_text"] == "Bravo"  # picked from options
    assert hist[-1]["option_index"] == 1


# -------------------------------
# Background scheduling behavior
# -------------------------------

@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_next_schedules_background_only_after_last_baseline(
    async_client, fake_redis, fake_cache_store, capture_background_tasks
):
    qid = uuid.uuid4()
    qs = [
        {"question_text": "Q0", "options": [{"text": "A"}, {"text": "B"}]},
        {"question_text": "Q1", "options": [{"text": "C"}, {"text": "D"}]},
        {"question_text": "Q2", "options": [{"text": "E"}, {"text": "F"}]},
    ]
    seed_quiz_state(
        fake_redis,
        qid,
        _valid_state(
            qid,
            trace_id="t-bg",
            generated_questions=qs,
            quiz_history=[],
            baseline_count=3,
            baseline_ready=True,
            ready_for_questions=True,
        ),
    )

    # Answer #0 → still baseline → no schedule
    r0 = await async_client.post(
        f"{api}/quiz/next",
        json={"quiz_id": str(qid), "question_index": 0, "option_index": 0},
    )
    assert r0.status_code == 202
    assert len(capture_background_tasks) == 0

    # Answer #1 → still baseline → no schedule
    r1 = await async_client.post(
        f"{api}/quiz/next",
        json={"quiz_id": str(qid), "question_index": 1, "option_index": 0},
    )
    assert r1.status_code == 202
    assert len(capture_background_tasks) == 0

    # Answer #2 (3rd baseline) → threshold reached → schedule exactly one task
    r2 = await async_client.post(
        f"{api}/quiz/next",
        json={"quiz_id": str(qid), "question_index": 2, "option_index": 0},
    )
    assert r2.status_code == 202
    assert len(capture_background_tasks) == 1

    func, args, kwargs = capture_background_tasks[0]
    assert func is run_agent_in_background
    task_state = args[0]
    assert isinstance(task_state, dict)
    assert str(task_state.get("session_id")) == str(qid)
    assert task_state.get("ready_for_questions") is True

    # History count persisted = 3
    cached = _load_cached_state(fake_cache_store, qid)
    assert len(cached.get("quiz_history") or []) == 3


# -------------------------------
# Contention path (atomic update conflict)
# -------------------------------

@pytest.mark.anyio
@pytest.mark.usefixtures("use_fake_agent_graph", "override_redis_dep")
async def test_next_409_when_atomic_update_conflicts(async_client, fake_redis, monkeypatch):
    """
    Force CacheRepository.update_quiz_state_atomically to return None,
    which should produce a 409 retriable error.
    """
    from app.api.endpoints import quiz as quiz_mod

    qid = uuid.uuid4()
    seed_quiz_state(
        fake_redis,
        qid,
        _valid_state(
            qid,
            trace_id="t-conflict",
            generated_questions=[{"question_text": "Q1", "options": [{"text": "A"}, {"text": "B"}]}],
            quiz_history=[],
            baseline_count=1,
            baseline_ready=True,
            ready_for_questions=True,
        ),
    )

    async def _return_none(_self, *_a, **_k):
        return None

    monkeypatch.setattr(
        quiz_mod.CacheRepository, "update_quiz_state_atomically", _return_none, raising=True
    )

    r = await async_client.post(
        f"{api}/quiz/next",
        json={"quiz_id": str(qid), "question_index": 0, "option_index": 0},
    )
    assert r.status_code == 409
    assert "retry" in r.text.lower() or "conflict" in r.text.lower()
