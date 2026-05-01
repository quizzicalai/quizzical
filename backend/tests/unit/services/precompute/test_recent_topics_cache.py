"""§21 Phase 10 — per-IP-hash recent topics cache (`AC-PRECOMP-OBJ-4`)."""

from __future__ import annotations

import fakeredis.aioredis as fr
import pytest

from app.services.precompute import recent_topics

pytestmark = pytest.mark.anyio


@pytest.fixture
async def redis():
    return fr.FakeRedis(decode_responses=False)


async def test_push_and_get_returns_last_seen_first(redis):
    for slug in ["a", "b", "c"]:
        await recent_topics.push_topic(redis, "iphash-1", slug)
    out = await recent_topics.get_recent(redis, "iphash-1")
    assert out == ["c", "b", "a"]


async def test_dedupe_promotes_existing_to_top(redis):
    for slug in ["a", "b", "c"]:
        await recent_topics.push_topic(redis, "h", slug)
    await recent_topics.push_topic(redis, "h", "a")
    out = await recent_topics.get_recent(redis, "h")
    assert out[0] == "a"
    # No duplicates.
    assert len(out) == len(set(out))


async def test_capped_at_max_recent(redis):
    for i in range(10):
        await recent_topics.push_topic(redis, "h", f"slug-{i}")
    out = await recent_topics.get_recent(redis, "h")
    assert len(out) == recent_topics.MAX_RECENT


async def test_failopen_with_none_redis_returns_empty():
    out = await recent_topics.get_recent(None, "h")
    assert out == []


async def test_per_ip_hash_isolation(redis):
    await recent_topics.push_topic(redis, "h1", "x")
    await recent_topics.push_topic(redis, "h2", "y")
    assert await recent_topics.get_recent(redis, "h1") == ["x"]
    assert await recent_topics.get_recent(redis, "h2") == ["y"]
