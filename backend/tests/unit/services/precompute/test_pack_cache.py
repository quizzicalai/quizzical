"""§21 Phase 4 — Redis pack cache + SETNX fill lock.

ACs covered:
- `AC-PRECOMP-PERF-2`: SETNX fill lock collapses N concurrent fillers
  into ≤ 1 underlying compute call (no thundering herd).
- transactional invalidation on publish / quarantine.
- fail-open on Redis errors.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import fakeredis.aioredis as fr
import pytest

from app.services.precompute import cache as pack_cache
from app.services.precompute.cache import (
    ResolvedPack,
    get_or_fill,
    get_pack,
    invalidate_pack,
    set_pack,
)

pytestmark = pytest.mark.anyio


def _pack(topic_id: str = "") -> ResolvedPack:
    tid = topic_id or str(uuid4())
    return ResolvedPack(
        topic_id=tid,
        pack_id=str(uuid4()),
        version=1,
        synopsis_id=str(uuid4()),
        character_set_id=str(uuid4()),
        baseline_question_set_id=str(uuid4()),
        storage_uris=("/api/v1/media/a", "/api/v1/media/b"),
    )


async def _fakeredis():
    return fr.FakeRedis(decode_responses=True)


async def test_set_get_roundtrip():
    r = await _fakeredis()
    p = _pack()
    assert await set_pack(r, p) is True
    got = await get_pack(r, p.topic_id)
    assert got is not None
    assert got.pack_id == p.pack_id
    assert got.storage_uris == p.storage_uris


async def test_get_pack_miss_returns_none():
    r = await _fakeredis()
    assert await get_pack(r, str(uuid4())) is None


async def test_invalidate_pack_removes_entry():
    r = await _fakeredis()
    p = _pack()
    await set_pack(r, p)
    assert await get_pack(r, p.topic_id) is not None
    assert await invalidate_pack(r, p.topic_id) is True
    assert await get_pack(r, p.topic_id) is None


async def test_setnx_fill_lock_prevents_thundering_herd():
    """`AC-PRECOMP-PERF-2` — 100 concurrent fillers ≤ 1 DB hit."""
    r = await _fakeredis()
    tid = str(uuid4())
    pack = _pack(topic_id=tid)
    call_count = 0

    async def slow_fill():
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)  # simulate JOIN
        return pack

    results = await asyncio.gather(
        *(get_or_fill(r, tid, slow_fill, max_wait_s=2.0) for _ in range(100))
    )
    assert call_count <= 1, f"thundering herd: {call_count} fills"
    assert all(rp is not None and rp.pack_id == pack.pack_id for rp in results)


async def test_get_or_fill_cache_hit_skips_fill():
    r = await _fakeredis()
    p = _pack()
    await set_pack(r, p)
    called = False

    async def fill():
        nonlocal called
        called = True
        return None

    got = await get_or_fill(r, p.topic_id, fill)
    assert got is not None
    assert got.pack_id == p.pack_id
    assert called is False


async def test_redis_error_falls_back_to_compute():
    """Fail-open: a broken Redis client must not break the request."""

    class _Broken:
        async def get(self, *_a, **_k):
            raise RuntimeError("boom")

        async def set(self, *_a, **_k):
            raise RuntimeError("boom")

        async def delete(self, *_a, **_k):
            raise RuntimeError("boom")

    p = _pack()

    async def fill():
        return p

    got = await get_or_fill(_Broken(), p.topic_id, fill)
    assert got is not None
    assert got.pack_id == p.pack_id


async def test_invalidate_after_publish_round_trip():
    """Publish → cache → invalidate → next read MISS → re-fill new pack.

    Models the transactional invalidation path: when `publish()` writes
    a new version, it must remove the stale cached pack so the next
    request observes the swap."""
    r = await _fakeredis()
    tid = str(uuid4())
    v1 = ResolvedPack(
        topic_id=tid, pack_id=str(uuid4()), version=1,
        synopsis_id="s1", character_set_id="c1", baseline_question_set_id="b1",
    )
    v2 = ResolvedPack(
        topic_id=tid, pack_id=str(uuid4()), version=2,
        synopsis_id="s2", character_set_id="c2", baseline_question_set_id="b2",
    )
    await set_pack(r, v1)
    assert (await get_pack(r, tid)).pack_id == v1.pack_id
    await invalidate_pack(r, tid)
    # Refill should observe the new version.
    async def fill():
        return v2
    got = await get_or_fill(r, tid, fill)
    assert got.pack_id == v2.pack_id
    assert got.version == 2


def test_resolved_pack_json_round_trip():
    p = _pack()
    raw = p.to_json()
    back = ResolvedPack.from_json(raw)
    assert back == p


def test_resolved_pack_from_invalid_json_returns_none():
    assert ResolvedPack.from_json("not-json") is None
    assert ResolvedPack.from_json('{"missing":"fields"}') is None


def test_build_link_header_preloads_images():
    h = pack_cache.build_link_header(("/a.png", "/b.png"))
    assert "rel=preload" in h
    assert "as=image" in h
    assert "/a.png" in h and "/b.png" in h


def test_build_link_header_empty_returns_empty_string():
    assert pack_cache.build_link_header(()) == ""
    assert pack_cache.build_link_header([""]) == ""


def test_build_link_header_caps_at_max_links():
    uris = tuple(f"/u{i}.png" for i in range(20))
    h = pack_cache.build_link_header(uris, max_links=3)
    assert h.count(",") == 2  # 3 entries → 2 separators


def test_collect_storage_uris_dedups():
    p = ResolvedPack(
        topic_id="t", pack_id="p", version=1,
        synopsis_id="s", character_set_id="c", baseline_question_set_id="b",
        storage_uris=("/a", "/b", "/a", ""),
    )
    assert pack_cache.collect_storage_uris(p) == ("/a", "/b")
    assert pack_cache.collect_storage_uris(None) == ()
    assert pack_cache.collect_storage_uris({"storage_uris": ["/x"]}) == ("/x",)
