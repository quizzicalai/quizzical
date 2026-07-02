"""P11 (2026-07-02) — Redis cache for the fully hydrated pack (`tk:hpack:*`).

The /quiz/start precompute short-circuit assembles a ``HydratedPack`` via ~5
serial DB queries; these tests pin the cache layer that absorbs that cost:

- set/get JSON roundtrip preserves every field (synopsis, characters,
  baseline questions).
- MISS and corrupt-payload reads return ``None`` (caller falls back to DB).
- All helpers are fail-open on Redis faults (broken client → MISS / False,
  never an exception).
- ``invalidate_hydrated_pack`` removes the entry (the starter-pack importer
  calls it so re-imported character art never serves stale).
"""

from __future__ import annotations

import uuid

import fakeredis.aioredis as fr
import pytest

from app.services.precompute.cache import (
    HYDRATED_PACK_KEY_FMT,
    get_hydrated_pack,
    invalidate_hydrated_pack,
    set_hydrated_pack,
)
from app.services.precompute.hydrator import HydratedPack

pytestmark = pytest.mark.anyio


def _hydrated() -> HydratedPack:
    return HydratedPack(
        pack_id=uuid.uuid4(),
        topic_id=uuid.uuid4(),
        synopsis={"title": "SC Title", "summary": "SC summary."},
        characters=(
            {
                "name": "Alpha",
                "short_description": "short",
                "profile_text": "long profile",
                "image_url": "https://cdn.example/a.png",
            },
            {
                "name": "Beta",
                "short_description": "short 2",
                "profile_text": "long profile 2",
                "image_url": None,
            },
        ),
        baseline_questions=(
            {
                "question_text": "Q1?",
                "options": [{"text": "a"}, {"text": "b"}],
                "progress_phrase": "warming up",
            },
        ),
    )


async def _fakeredis():
    return fr.FakeRedis(decode_responses=True)


class _BrokenRedis:
    """Every command raises — exercises the fail-open contract."""

    async def get(self, *_a, **_k):
        raise ConnectionError("redis down")

    async def set(self, *_a, **_k):
        raise ConnectionError("redis down")

    async def delete(self, *_a, **_k):
        raise ConnectionError("redis down")


async def test_set_get_roundtrip_preserves_all_fields():
    r = await _fakeredis()
    p = _hydrated()
    assert await set_hydrated_pack(r, p) is True

    got = await get_hydrated_pack(r, p.pack_id)
    assert got is not None
    assert got.pack_id == p.pack_id
    assert got.topic_id == p.topic_id
    assert got.synopsis == p.synopsis
    assert got.characters == p.characters
    assert got.baseline_questions == p.baseline_questions


async def test_get_miss_returns_none():
    r = await _fakeredis()
    assert await get_hydrated_pack(r, uuid.uuid4()) is None


async def test_get_corrupt_payload_returns_none():
    r = await _fakeredis()
    pack_id = uuid.uuid4()
    await r.set(HYDRATED_PACK_KEY_FMT.format(pack_id=pack_id), "{not json")
    assert await get_hydrated_pack(r, pack_id) is None
    # Valid JSON with missing required keys is also treated as a MISS.
    await r.set(HYDRATED_PACK_KEY_FMT.format(pack_id=pack_id), '{"pack_id": "x"}')
    assert await get_hydrated_pack(r, pack_id) is None


async def test_invalidate_removes_entry():
    r = await _fakeredis()
    p = _hydrated()
    await set_hydrated_pack(r, p)
    assert await get_hydrated_pack(r, p.pack_id) is not None
    assert await invalidate_hydrated_pack(r, p.pack_id) is True
    assert await get_hydrated_pack(r, p.pack_id) is None


async def test_all_helpers_fail_open_on_redis_errors():
    broken = _BrokenRedis()
    p = _hydrated()
    assert await get_hydrated_pack(broken, p.pack_id) is None
    assert await set_hydrated_pack(broken, p) is False
    assert await invalidate_hydrated_pack(broken, p.pack_id) is False


async def test_helpers_treat_none_redis_as_miss():
    p = _hydrated()
    assert await get_hydrated_pack(None, p.pack_id) is None
    assert await set_hydrated_pack(None, p) is False
    assert await invalidate_hydrated_pack(None, p.pack_id) is False


async def test_ttl_is_applied():
    r = await _fakeredis()
    p = _hydrated()
    await set_hydrated_pack(r, p, ttl_s=1234)
    ttl = await r.ttl(HYDRATED_PACK_KEY_FMT.format(pack_id=p.pack_id))
    assert 0 < ttl <= 1234
