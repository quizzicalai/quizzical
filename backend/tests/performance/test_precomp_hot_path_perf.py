"""§21 Phase 4 — pack lookup hot-path performance.

`AC-PRECOMP-PERF-4` — p95 pack lookup ≤ 25 ms when served from the
Redis cache (HIT path). The benchmark uses `fakeredis` to isolate the
cache layer's wire-protocol cost from any network. The bound is
intentionally loose because CI runners vary; if this test ever fails
on a real machine, the cache layer regressed.
"""

from __future__ import annotations

import asyncio
import time
from uuid import uuid4

import fakeredis.aioredis as fr
import pytest

from app.services.precompute.cache import (
    ResolvedPack,
    get_or_fill,
    set_pack,
)

pytestmark = pytest.mark.anyio


def _pack() -> ResolvedPack:
    return ResolvedPack(
        topic_id=str(uuid4()),
        pack_id=str(uuid4()),
        version=1,
        synopsis_id=str(uuid4()),
        character_set_id=str(uuid4()),
        baseline_question_set_id=str(uuid4()),
        storage_uris=("/api/v1/media/a", "/api/v1/media/b"),
    )


async def test_pack_lookup_p95_under_25ms():
    r = fr.FakeRedis(decode_responses=True)
    p = _pack()
    await set_pack(r, p)

    async def fill():  # never called on HIT
        raise AssertionError("fill called on HIT")

    samples_ms: list[float] = []
    # Warm-up
    for _ in range(20):
        await get_or_fill(r, p.topic_id, fill)
    # Measured
    for _ in range(200):
        t0 = time.perf_counter()
        await get_or_fill(r, p.topic_id, fill)
        samples_ms.append((time.perf_counter() - t0) * 1000.0)

    samples_ms.sort()
    p95 = samples_ms[int(len(samples_ms) * 0.95) - 1]
    assert p95 < 25.0, f"p95={p95:.2f}ms exceeds 25ms budget; samples={samples_ms[-5:]}"


async def test_concurrent_hits_complete_within_budget():
    """100 concurrent HITs all complete under 1 s wall-clock."""
    r = fr.FakeRedis(decode_responses=True)
    p = _pack()
    await set_pack(r, p)

    async def fill():
        raise AssertionError("should not fill on HIT")

    t0 = time.perf_counter()
    results = await asyncio.gather(
        *(get_or_fill(r, p.topic_id, fill) for _ in range(100))
    )
    elapsed = time.perf_counter() - t0
    assert all(rp is not None and rp.pack_id == p.pack_id for rp in results)
    assert elapsed < 1.0, f"100 concurrent HITs took {elapsed:.3f}s"
