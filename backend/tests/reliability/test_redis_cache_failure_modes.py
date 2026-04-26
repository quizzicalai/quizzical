"""
Iteration 2 — Reliability: Redis cache repository must fail safely.

When Redis raises, the repository must:
- log the failure
- never propagate the exception to the caller
- return a sentinel (None) for reads/updates so the app degrades gracefully
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from redis.exceptions import RedisError

from app.services.redis_cache import CacheRepository


def _client_with(**method_side_effects):
    client = MagicMock()
    for name, side in method_side_effects.items():
        setattr(client, name, AsyncMock(side_effect=side))
    return client


# ---------------------------------------------------------------------------
# get_quiz_state
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_quiz_state_returns_none_on_redis_error():
    client = _client_with(get=RedisError("boom"))
    repo = CacheRepository(client)
    result = await repo.get_quiz_state(uuid.uuid4())
    assert result is None


@pytest.mark.asyncio
async def test_get_quiz_state_returns_none_on_invalid_json():
    client = MagicMock()
    client.get = AsyncMock(return_value="{not-valid-json")
    repo = CacheRepository(client)
    result = await repo.get_quiz_state(uuid.uuid4())
    assert result is None


# ---------------------------------------------------------------------------
# save_quiz_state
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_save_quiz_state_no_session_id_is_noop():
    client = MagicMock()
    client.set = AsyncMock()
    repo = CacheRepository(client)
    await repo.save_quiz_state({})  # no session_id
    client.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_quiz_state_swallows_redis_error():
    """Even if Redis SET fails, the repository must not raise."""
    sid = uuid.uuid4()
    client = MagicMock()
    client.set = AsyncMock(side_effect=RedisError("write failed"))

    repo = CacheRepository(client)
    state = {
        "session_id": sid,
        "category": "test",
        "synopsis": {"title": "t", "summary": "s"},
        "messages": [],
    }
    # Must NOT raise
    await repo.save_quiz_state(state, ttl_seconds=60)


# ---------------------------------------------------------------------------
# RAG cache
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_rag_cache_returns_none_on_redis_error():
    client = _client_with(get=RedisError("boom"))
    repo = CacheRepository(client)
    assert await repo.get_rag_cache("any") is None


@pytest.mark.asyncio
async def test_get_rag_cache_returns_none_on_miss():
    client = MagicMock()
    client.get = AsyncMock(return_value=None)
    repo = CacheRepository(client)
    assert await repo.get_rag_cache("any") is None


@pytest.mark.asyncio
async def test_set_rag_cache_swallows_redis_error():
    client = _client_with(set=RedisError("write failed"))
    repo = CacheRepository(client)
    # Must NOT raise
    await repo.set_rag_cache("slug", "payload", ttl_seconds=10)


@pytest.mark.asyncio
async def test_set_rag_cache_passes_ttl():
    client = MagicMock()
    client.set = AsyncMock()
    repo = CacheRepository(client)
    await repo.set_rag_cache("slug", "payload", ttl_seconds=42)
    args, kwargs = client.set.call_args
    # ttl is passed via the `ex` kwarg
    assert kwargs.get("ex") == 42


# ---------------------------------------------------------------------------
# update_quiz_state_atomically
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_quiz_state_returns_none_on_pipeline_error():
    """If pipeline.watch raises a RedisError the call must return None, not raise."""
    sid = uuid.uuid4()

    pipe = MagicMock()
    pipe.__aenter__ = AsyncMock(return_value=pipe)
    pipe.__aexit__ = AsyncMock(return_value=False)
    pipe.watch = AsyncMock(side_effect=RedisError("watch failed"))
    pipe.unwatch = AsyncMock()
    pipe.reset = MagicMock()

    client = MagicMock()
    client.pipeline = MagicMock(return_value=pipe)

    repo = CacheRepository(client)
    result = await repo.update_quiz_state_atomically(sid, {"category": "x"}, ttl_seconds=60)
    assert result is None
