import json
import uuid
import pytest

from types import SimpleNamespace

from app.services.redis_cache import (
    _ensure_text,
    _message_to_dict,
    _normalize_graph_state_for_storage,
    CacheRepository,
)
from app.agent.schemas import AgentGraphStateModel
from tests.fixtures.redis_fixtures import seed_quiz_state

# ----------------------
# Small utility tests
# ----------------------

def test_ensure_text_handles_bytes_and_str():
    assert _ensure_text("hello") == "hello"
    assert _ensure_text(b"world") == "world"

def test_message_to_dict_passthrough_and_duck_typing():
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
    # Includes a duck-typed message and a plain dict, both should be normalized
    msg1 = SimpleNamespace(content="hi", type="human")
    msg2 = {"type": "ai", "content": "hello"}
    state = {"session_id": str(uuid.uuid4()), "messages": [msg1, msg2]}
    out = _normalize_graph_state_for_storage(state)
    assert isinstance(out, dict)
    assert isinstance(out["messages"], list)
    assert out["messages"][0] == {"type": "human", "content": "hi"}
    assert out["messages"][1] == {"type": "ai", "content": "hello"}

# ----------------------
# CacheRepository tests
# ----------------------

@pytest.mark.asyncio
async def test_save_and_get_quiz_state_roundtrip(fake_redis, fake_cache_store, ids):
    repo = CacheRepository(fake_redis)

    # Use the fixture’s IDs
    session_id_str = ids["session_id"]
    trace_id = ids["trace_id"]
    session_uuid = uuid.UUID(session_id_str)  # needed if you later call get_quiz_state()

    state = {
        "session_id": session_id_str,          # str is fine for storage
        "trace_id": trace_id,                  # <-- required by AgentGraphStateModel
        "messages": [{"type": "human", "content": "start"}],
        "category": "Cats",
    }

    await repo.save_quiz_state(state, ttl_seconds=123)

    # Key should now exist
    key = f"quiz_session:{session_id_str}"
    assert key in fake_cache_store

    # Optional: read back through the repo API
    got = await repo.get_quiz_state(session_uuid)  # takes uuid.UUID
    assert got is not None
    assert got.session_id == session_uuid
    assert got.trace_id == trace_id
    assert got.category == "Cats"
    assert got.messages[0]["content"] == "start"

@pytest.mark.asyncio
async def test_save_quiz_state_without_session_id_is_noop(fake_redis, fake_cache_store):
    repo = CacheRepository(fake_redis)
    state = {
        # no session_id
        "messages": [{"type": "human", "content": "hello"}],
    }
    await repo.save_quiz_state(state)
    # Ensure nothing was written
    assert not any(k.startswith("quiz_session:") for k in fake_cache_store.keys())

@pytest.mark.asyncio
async def test_get_quiz_state_returns_none_when_missing(fake_redis):
    repo = CacheRepository(fake_redis)
    missing_id = uuid.uuid4()
    got = await repo.get_quiz_state(missing_id)
    assert got is None

@pytest.mark.asyncio
async def test_update_quiz_state_atomically_success(fake_redis, fake_cache_store, ids):
    repo = CacheRepository(fake_redis)
    session_id = uuid.uuid4()

    # Seed with a valid, schema-friendly JSON blob
    initial = {
        "session_id": str(session_id),
        "trace_id": ids["trace_id"],
        "messages": [{"type": "human", "content": "hi"}],
        "category": "Dogs",
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
    # Messages should be a list of dicts; last message should be our AI message
    assert isinstance(persisted.get("messages"), list)
    assert persisted["messages"][-1]["type"] == "ai"
    assert persisted["messages"][-1]["content"] == "welcome"

@pytest.mark.asyncio
async def test_update_quiz_state_atomically_returns_none_when_missing(fake_redis):
    repo = CacheRepository(fake_redis)
    missing_id = uuid.uuid4()
    out = await repo.update_quiz_state_atomically(missing_id, {"category": "Birds"})
    assert out is None

@pytest.mark.asyncio
async def test_rag_cache_set_and_get(fake_redis):
    repo = CacheRepository(fake_redis)
    slug = "cozy-tv"
    content = "cached RAG text"

    # Miss before set
    assert await repo.get_rag_cache(slug) is None

    await repo.set_rag_cache(slug, content, ttl_seconds=321)

    # Hit after set
    got = await repo.get_rag_cache(slug)
    assert got == content
