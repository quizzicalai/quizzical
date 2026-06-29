"""Audit P1 (#48) — the missing concurrency test.

The background agent (``run_agent_in_background``) used to finish by calling
``CacheRepository.save_quiz_state`` — a FULL Redis SET of the whole snapshot.
That clobbered any concurrent atomic merges that ``/quiz/next`` (records an
answer into ``quiz_history``/``messages``) and ``/quiz/status`` (advances
``last_served_index``) performed via ``update_quiz_state_atomically`` while the
agent was mid-run (the run takes several seconds). A delayed ``/next`` or
``/status`` write that landed during the run was silently dropped, losing a
recorded answer or reverting the served pointer.

Fix: the agent's final persistence (``_save_final_state_to_cache``) now merges
ONLY the agent-owned fields via ``update_quiz_state_atomically``, explicitly
excluding the request-owned fields (``quiz_history``, ``messages``,
``last_served_index``, ``ready_for_questions``).

These tests exercise the fix against the in-memory fake-redis (with real
WATCH/MULTI semantics) by simulating the race directly: seed an initial state,
apply a concurrent ``/next``-style atomic update, then run the agent's
field-scoped save and assert the concurrent write survives while the agent's
new fields are applied.
"""
from __future__ import annotations

import json
import uuid

import pytest

from app.api.endpoints.quiz import (
    _AGENT_OWNED_STATE_FIELDS,
    _REQUEST_OWNED_STATE_FIELDS,
    _save_final_state_to_cache,
)
from app.services.redis_cache import CacheRepository
from tests.fixtures.redis_fixtures import seed_quiz_state


def _base_state(quiz_id: uuid.UUID) -> dict:
    """A minimal-but-valid AgentGraphStateModel-shaped dict to seed Redis."""
    return {
        "session_id": str(quiz_id),
        "trace_id": "t-p1-merge",
        "category": "Cats",
        "messages": [{"type": "human", "content": "Cats"}],
        "synopsis": {"title": "Quiz: Cats", "summary": "Meow"},
        "generated_characters": [],
        "ideal_archetypes": [],
        # Agent has only baseline questions so far.
        "generated_questions": [
            {"question_text": "Q1?", "options": [{"text": "a"}, {"text": "b"}]},
        ],
        "quiz_history": [],
        "baseline_count": 1,
        "baseline_ready": True,
        "ready_for_questions": True,
        "final_result": None,
        "last_served_index": None,
        "error_count": 0,
        "is_error": False,
    }


def _read_cached(fake_cache_store, quiz_id: uuid.UUID) -> dict:
    key = f"quiz_session:{quiz_id}"
    return json.loads(fake_cache_store[key])


@pytest.mark.asyncio
async def test_agent_save_preserves_concurrent_quiz_history(fake_redis, fake_cache_store):
    """The race: a /next-style atomic merge records an answer into
    ``quiz_history``/``messages`` AFTER the agent captured its (stale) snapshot
    but BEFORE the agent's final save. The agent's save must NOT clobber the
    recorded answer, while its own new fields ARE applied.
    """
    quiz_id = uuid.uuid4()
    repo = CacheRepository(fake_redis)

    # 1) Seed initial live state (what the agent read when it started).
    seed_quiz_state(fake_redis, quiz_id, _base_state(quiz_id))

    # 2) Concurrent /quiz/next lands DURING the agent run: it atomically merges
    #    a recorded answer. (This is exactly what next_question() does.)
    recorded_history = [
        {
            "question_index": 0,
            "question_text": "Q1?",
            "answer_text": "a",
            "option_index": 0,
        }
    ]
    recorded_messages = [
        {"type": "human", "content": "Cats"},
        {"type": "human", "content": "Answer to Q1: a"},
    ]
    merged = await repo.update_quiz_state_atomically(
        quiz_id,
        {
            "quiz_history": recorded_history,
            "messages": recorded_messages,
            "ready_for_questions": True,
        },
    )
    assert merged is not None

    # 3) The agent finishes. Its in-memory final_state is STALE: it still has
    #    the empty quiz_history it started with, plus newly-generated adaptive
    #    questions and a final result.
    agent_final_state = _base_state(quiz_id)
    agent_final_state["quiz_history"] = []  # stale — would clobber under full SET
    agent_final_state["messages"] = [{"type": "human", "content": "Cats"}]  # stale
    agent_final_state["generated_questions"] = [
        {"question_text": "Q1?", "options": [{"text": "a"}, {"text": "b"}]},
        {"question_text": "Q2 (adaptive)?", "options": [{"text": "x"}, {"text": "y"}]},
    ]
    agent_final_state["baseline_count"] = 1
    agent_final_state["final_result"] = {
        "title": "You are a Tabby",
        "description": "x" * 420,  # FinalResult requires >= 400 chars
    }

    # 4) Run the agent's field-scoped save.
    await _save_final_state_to_cache(repo, str(quiz_id), agent_final_state)

    # 5) Assertions on the persisted state.
    persisted = _read_cached(fake_cache_store, quiz_id)

    # The concurrent answer SURVIVED (not reverted to the agent's stale []).
    assert persisted["quiz_history"] == recorded_history, (
        "agent save clobbered the concurrently-recorded quiz_history"
    )
    assert len(persisted["messages"]) == 2, (
        "agent save clobbered the concurrently-recorded messages"
    )

    # The agent's OWN fields WERE applied.
    assert len(persisted["generated_questions"]) == 2, (
        "agent's newly-generated adaptive question was not applied"
    )
    assert persisted["final_result"] is not None
    assert persisted["final_result"]["title"] == "You are a Tabby"


@pytest.mark.asyncio
async def test_agent_save_preserves_concurrent_last_served_index(fake_redis, fake_cache_store):
    """A /quiz/status poll advanced ``last_served_index`` during the agent run.
    The agent's save must not revert that request-owned pointer.
    """
    quiz_id = uuid.uuid4()
    repo = CacheRepository(fake_redis)

    seed_quiz_state(fake_redis, quiz_id, _base_state(quiz_id))

    # /status advances the pointer atomically while the agent works.
    await repo.update_quiz_state_atomically(quiz_id, {"last_served_index": 0})

    # Agent finishes with a stale pointer (None) plus new fields.
    agent_final_state = _base_state(quiz_id)
    agent_final_state["last_served_index"] = None  # stale
    agent_final_state["current_confidence"] = 0.42
    agent_final_state["should_finalize"] = True

    await _save_final_state_to_cache(repo, str(quiz_id), agent_final_state)

    persisted = _read_cached(fake_cache_store, quiz_id)

    # Pointer preserved (not reverted to None).
    assert persisted["last_served_index"] == 0, (
        "agent save reverted the concurrently-advanced last_served_index"
    )
    # Agent-owned fields applied.
    assert persisted["current_confidence"] == 0.42
    assert persisted["should_finalize"] is True


@pytest.mark.asyncio
async def test_agent_save_never_sets_request_owned_fields(fake_redis, fake_cache_store):
    """Guardrail: even if a stale request-owned value is present in the agent's
    final state, it must NEVER be part of the merge written to Redis.
    """
    quiz_id = uuid.uuid4()
    repo = CacheRepository(fake_redis)

    seed_quiz_state(fake_redis, quiz_id, _base_state(quiz_id))

    # Two concurrent request writes land first.
    recorded_history = [
        {
            "question_index": 0,
            "question_text": "Q1?",
            "answer_text": "b",
            "option_index": 1,
        }
    ]
    await repo.update_quiz_state_atomically(
        quiz_id, {"quiz_history": recorded_history, "last_served_index": 0}
    )

    # Agent final state carries STALE values for ALL request-owned fields.
    agent_final_state = _base_state(quiz_id)
    agent_final_state["quiz_history"] = []
    agent_final_state["messages"] = []
    agent_final_state["last_served_index"] = None
    agent_final_state["ready_for_questions"] = False
    agent_final_state["final_result"] = {
        "title": "Done",
        "description": "y" * 420,
    }

    await _save_final_state_to_cache(repo, str(quiz_id), agent_final_state)

    persisted = _read_cached(fake_cache_store, quiz_id)

    # Every request-owned field kept its concurrently-written value (or, for
    # ``messages``, the value present before the agent save — never the agent's
    # stale [] ).
    assert persisted["quiz_history"] == recorded_history
    assert persisted["last_served_index"] == 0
    # The concurrent update above didn't touch ``messages``, so it retains the
    # seeded single 'Cats' message — and crucially is NOT clobbered to the
    # agent's stale empty list.
    assert len(persisted["messages"]) == 1
    # ``ready_for_questions`` stays True (set during seed), not flipped back to
    # the agent's stale False.
    assert persisted["ready_for_questions"] is True

    # Agent's own field applied.
    assert persisted["final_result"]["title"] == "Done"


def test_request_owned_fields_excluded_from_agent_owned_set():
    """Static guard: the agent-owned and request-owned field sets are disjoint,
    so the merge can never include a request-owned field.
    """
    overlap = set(_AGENT_OWNED_STATE_FIELDS) & set(_REQUEST_OWNED_STATE_FIELDS)
    assert overlap == set(), f"agent-owned set must not include request-owned fields: {overlap}"
