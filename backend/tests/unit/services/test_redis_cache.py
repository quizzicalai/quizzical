# backend/tests/unit/services/test_redis_cache.py

import json
import uuid
from types import SimpleNamespace

import pytest

from app.agent.schemas import AgentGraphStateModel
from app.services.redis_cache import (
    CacheRepository,
    _ensure_text,
    _message_to_dict,
    _normalize_graph_state_for_storage,
)
from tests.fixtures.redis_fixtures import seed_quiz_state

# ----------------------
# Small utility tests
# ----------------------

def test_ensure_text_handles_bytes_and_str():
    """Verify byte decoding and string passthrough."""
    assert _ensure_text("hello") == "hello"
    assert _ensure_text(b"world") == "world"


def test_message_to_dict_passthrough_and_duck_typing():
    """Verify message normalization logic."""
    # 1) Pass-through dict
    d = {"type": "human", "content": "hi"}
    assert _message_to_dict(d) is d

    # 2) Duck-typed object like a LangChain message
    obj = SimpleNamespace(
        content="yo",
        type="ai",
        name="assistant",
        additional_kwargs={"foo": "bar"},
    )
    out = _message_to_dict(obj)
    assert out["type"] == "ai"
    assert out["content"] == "yo"
    assert out["name"] == "assistant"
    assert out["additional_kwargs"] == {"foo": "bar"}


def test_normalize_graph_state_for_storage_messages_mapped():
    """Verify that graph state normalization handles message objects."""
    msg1 = SimpleNamespace(content="hi", type="human")
    msg2 = {"type": "ai", "content": "hello"}

    # We need enough fields to pass Pydantic validation later if we were to validate,
    # but this helper just normalizes dicts.
    state = {"session_id": str(uuid.uuid4()), "messages": [msg1, msg2]}

    out = _normalize_graph_state_for_storage(state)
    assert isinstance(out, dict)
    assert isinstance(out["messages"], list)
    assert out["messages"][0] == {"type": "human", "content": "hi"}
    assert out["messages"][1] == {"type": "ai", "content": "hello"}


def test_normalize_graph_state_drops_legacy_analysis_and_unknown_keys():
    """Regression for prod ``redis.save_state.fail`` (2026-05-25):

    The agent's TypedDict ``GraphState`` legitimately carries transient
    working keys (``analysis``, ``topic_knowledge``, tool scratchpads, …)
    that are NOT part of the canonical ``AgentGraphStateModel`` schema —
    which uses ``extra='forbid'``. Without filtering, ``save_quiz_state``
    crashed with ``ValidationError(extra_forbidden)`` mid-quiz and the
    resumed session 404'd, surfacing as the generic 4xx "Something went
    wrong" toast on the client.
    """
    sid = str(uuid.uuid4())
    state = {
        "session_id": sid,
        "trace_id": "t",
        "category": "Cats",
        # Legacy ephemeral key written by the agent's planning nodes.
        "analysis": {
            "normalized_category": "Cats",
            "characters": ["Tabby"],
            "names_only": True,
        },
        # Other tool-scratchpad keys observed in prod logs.
        "topic_knowledge": {"is_well_known": True},
        "unknown_future_key": 42,
    }

    out = _normalize_graph_state_for_storage(state)

    # Legacy/unknown keys must be stripped so model_validate succeeds.
    assert "analysis" not in out
    assert "topic_knowledge" not in out
    assert "unknown_future_key" not in out

    # The legacy ``analysis`` payload is migrated to ``topic_analysis``
    # (the canonical field name) so the planner's normalization decision
    # isn't lost across the round-trip.
    assert out["topic_analysis"]["normalized_category"] == "Cats"

    # And the result actually validates against the canonical schema.
    AgentGraphStateModel.model_validate(out)


# ----------------------
# CacheRepository tests
# ----------------------

@pytest.mark.asyncio
async def test_save_and_get_quiz_state_roundtrip(fake_redis, fake_cache_store):
    """Verify saving and retrieving a full state object."""
    repo = CacheRepository(fake_redis)

    session_id = uuid.uuid4()
    trace_id = "test-trace-1"

    # Create a valid state dict conforming to AgentGraphStateModel
    state = {
        "session_id": session_id,
        "trace_id": trace_id,
        "category": "Cats",
        "messages": [{"type": "human", "content": "start"}],
        "synopsis": {"title": "Quiz: Cats", "summary": "Meow"},
    }

    await repo.save_quiz_state(state, ttl_seconds=123)

    # Key should now exist in the fake store
    key = f"quiz_session:{session_id}"
    assert key in fake_cache_store

    # Retrieve back through the repo API
    got = await repo.get_quiz_state(session_id)

    assert isinstance(got, AgentGraphStateModel)
    assert got.session_id == session_id
    assert got.trace_id == trace_id
    assert got.category == "Cats"
    assert got.messages[0]["content"] == "start"
    assert got.synopsis.title == "Quiz: Cats"


@pytest.mark.asyncio
async def test_status_snapshot_matches_full_state(fake_redis, fake_cache_store):
    """Hitlist #11 — the lightweight snapshot must surface the SAME field values
    the /status hot path used to read from the validated+dumped full state."""
    repo = CacheRepository(fake_redis)
    session_id = uuid.uuid4()

    state = {
        "session_id": session_id,
        "trace_id": "snap-trace",
        "category": "Cats",
        "messages": [{"type": "human", "content": "start"}],
        "synopsis": {"title": "Quiz: Cats", "summary": "Meow"},
        "generated_questions": [
            {"question_text": "Q1?", "options": [{"text": "a"}, {"text": "b"}]},
            {"question_text": "Q2?", "options": [{"text": "c"}, {"text": "d"}]},
        ],
        "quiz_history": [{"question_text": "Q1?", "answer_text": "a"}],
        "current_confidence": 0.42,
        "last_served_index": 1,
        "final_result": None,
    }
    await repo.save_quiz_state(state)

    full = await repo.get_quiz_state(session_id)
    snap = await repo.get_quiz_status_snapshot(session_id)
    assert snap is not None

    dumped = full.model_dump()
    # Each snapshot field equals what the endpoint previously read from the
    # validated+dumped state.
    assert snap.trace_id == dumped["trace_id"]
    assert snap.final_result == dumped["final_result"]
    assert snap.current_confidence == dumped["current_confidence"]
    assert snap.last_served_index == dumped["last_served_index"]
    assert snap.quiz_history_len == len(dumped["quiz_history"])
    assert len(snap.generated_questions) == len(dumped["generated_questions"])
    # The single served question is byte-identical to the full-state element.
    assert snap.generated_questions[1] == dumped["generated_questions"][1]


@pytest.mark.asyncio
async def test_status_snapshot_returns_none_on_miss_and_garbage(fake_redis, fake_cache_store):
    """A cache miss and an unparsable payload both map to None so the endpoint
    falls back to the DB rehydrate path (never raises)."""
    repo = CacheRepository(fake_redis)

    missing = uuid.uuid4()
    assert await repo.get_quiz_status_snapshot(missing) is None

    # Garbage (non-JSON) payload at the live key -> None, not an exception.
    bad_id = uuid.uuid4()
    fake_cache_store[f"quiz_session:{bad_id}"] = "}{not json"
    assert await repo.get_quiz_status_snapshot(bad_id) is None


@pytest.mark.asyncio
async def test_status_snapshot_guards_missing_fields(fake_redis, fake_cache_store):
    """A minimal/partial stored state must not raise — missing list/scalar fields
    default safely."""
    repo = CacheRepository(fake_redis)
    session_id = uuid.uuid4()
    # Seed a deliberately sparse payload directly (no generated_questions / etc.).
    fake_cache_store[f"quiz_session:{session_id}"] = json.dumps(
        {"session_id": str(session_id)}
    )
    snap = await repo.get_quiz_status_snapshot(session_id)
    assert snap is not None
    assert snap.generated_questions == []
    assert snap.quiz_history_len == 0
    assert snap.final_result is None
    assert snap.current_confidence is None
    assert snap.last_served_index is None


@pytest.mark.asyncio
async def test_save_quiz_state_without_session_id_is_noop(fake_redis, fake_cache_store):
    """Verify that saving a state without session_id does nothing."""
    repo = CacheRepository(fake_redis)
    state = {
        "messages": [{"type": "human", "content": "hello"}],
        # Missing session_id
    }
    await repo.save_quiz_state(state)

    # Ensure nothing was written
    assert not any(k.startswith("quiz_session:") for k in fake_cache_store.keys())


@pytest.mark.asyncio
async def test_get_quiz_state_returns_none_when_missing(fake_redis):
    """Verify behavior on cache miss."""
    repo = CacheRepository(fake_redis)
    missing_id = uuid.uuid4()
    got = await repo.get_quiz_state(missing_id)
    assert got is None


@pytest.mark.asyncio
async def test_update_quiz_state_atomically_success(fake_redis, fake_cache_store):
    """Verify atomic update (read-modify-write loop)."""
    repo = CacheRepository(fake_redis)
    session_id = uuid.uuid4()
    trace_id = "t-atomic"

    # Seed with a valid JSON blob
    initial = {
        "session_id": str(session_id),
        "trace_id": trace_id,
        "category": "Dogs",
        "messages": [{"type": "human", "content": "hi"}],
        "synopsis": {"title": "Quiz: Dogs", "summary": "Woof"},
    }
    seed_quiz_state(fake_redis, session_id, initial)

    # Perform atomic update: change category and add a message
    new_data = {
        "category": "Cats",
        "messages": [{"type": "ai", "content": "welcome"}],  # will be normalized
    }

    updated = await repo.update_quiz_state_atomically(session_id, new_data, ttl_seconds=999)
    assert isinstance(updated, AgentGraphStateModel)

    dumped = updated.model_dump()
    assert dumped.get("category") == "Cats"
    assert str(dumped.get("session_id")) == str(session_id)

    # Ensure persisted value is updated in the fake store
    key = f"quiz_session:{session_id}"
    persisted = json.loads(fake_cache_store[key])
    assert persisted.get("category") == "Cats"

    # Messages should have been updated (shallow merge behavior depends on implementation)
    # In your redis_cache.py implementation: current_state.update(new_data) is a shallow merge.
    # So "messages" key is *replaced* by the new list.
    assert isinstance(persisted.get("messages"), list)
    assert len(persisted["messages"]) == 1
    assert persisted["messages"][0]["type"] == "ai"
    assert persisted["messages"][0]["content"] == "welcome"


@pytest.mark.asyncio
async def test_update_quiz_state_atomically_returns_none_when_missing(fake_redis):
    """Verify atomic update fails gracefully if key is missing."""
    repo = CacheRepository(fake_redis)
    missing_id = uuid.uuid4()
    out = await repo.update_quiz_state_atomically(missing_id, {"category": "Birds"})
    assert out is None


@pytest.mark.asyncio
async def test_rag_cache_set_and_get(fake_redis):
    """Verify simple RAG string caching."""
    repo = CacheRepository(fake_redis)
    slug = "cozy-tv"
    content = "cached RAG text"

    # Miss before set
    assert await repo.get_rag_cache(slug) is None

    await repo.set_rag_cache(slug, content, ttl_seconds=321)

    # Hit after set
    got = await repo.get_rag_cache(slug)
    assert got == content
