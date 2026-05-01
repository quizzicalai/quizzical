"""§21 Phase 4 — hot-character pinning (`AC-PRECOMP-PERF-6`)."""

from __future__ import annotations

from uuid import uuid4

import fakeredis.aioredis as fr
import pytest

from app.services.precompute.cache import (
    HOT_CHAR_KEY_FMT,
    HOT_CHAR_REF_THRESHOLD,
    maybe_pin_hot_character,
)

pytestmark = pytest.mark.anyio


async def _r():
    return fr.FakeRedis(decode_responses=True)


async def test_character_pinned_when_ref_count_above_threshold():
    r = await _r()
    aid = str(uuid4())
    pinned = await maybe_pin_hot_character(
        r, asset_id=aid, storage_uri="/api/v1/media/x",
        ref_count=HOT_CHAR_REF_THRESHOLD + 1,
    )
    assert pinned is True
    val = await r.get(HOT_CHAR_KEY_FMT.format(asset_id=aid))
    assert val == "/api/v1/media/x"


async def test_character_not_pinned_below_threshold():
    r = await _r()
    aid = str(uuid4())
    pinned = await maybe_pin_hot_character(
        r, asset_id=aid, storage_uri="/api/v1/media/y",
        ref_count=HOT_CHAR_REF_THRESHOLD - 1,
    )
    assert pinned is False
    assert await r.get(HOT_CHAR_KEY_FMT.format(asset_id=aid)) is None


async def test_character_pin_at_exact_threshold():
    r = await _r()
    aid = str(uuid4())
    assert await maybe_pin_hot_character(
        r, asset_id=aid, storage_uri="/u",
        ref_count=HOT_CHAR_REF_THRESHOLD,
    ) is True


async def test_pin_fails_open_on_redis_error():
    class _Broken:
        async def set(self, *_a, **_k):
            raise RuntimeError("nope")

    pinned = await maybe_pin_hot_character(
        _Broken(), asset_id=str(uuid4()), storage_uri="/u",
        ref_count=HOT_CHAR_REF_THRESHOLD + 50,
    )
    assert pinned is False  # never raises
