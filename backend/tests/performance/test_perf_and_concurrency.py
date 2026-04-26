"""
Iteration 3 — Performance & concurrency.

Validates:
- /health and /readiness respond quickly with no work to do.
- Concurrent /health requests are independent (unique trace ids, no shared state leaks).
- Concurrent /quiz/start requests get distinct quiz IDs and don't cross-contaminate.
- Redis cache atomic update retries on WATCH conflicts and gives up gracefully.
- Backoff helper is bounded.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from redis.exceptions import WatchError

from app.main import API_PREFIX
from app.services.redis_cache import CacheRepository, _jittered_backoff
from tests.helpers.sample_payloads import start_quiz_payload


# ---------------------------------------------------------------------------
# Latency budgets for cheap endpoints
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_health_responds_under_250ms(client):
    # warm-up
    await client.get("/health")
    t0 = time.perf_counter()
    resp = await client.get("/health")
    elapsed = time.perf_counter() - t0
    assert resp.status_code == 200
    assert elapsed < 0.25, f"/health took {elapsed:.3f}s; budget 0.25s"


@pytest.mark.anyio
async def test_readiness_short_circuits_when_no_deps(client, monkeypatch):
    """With neither DB engine nor Redis pool, /readiness should not perform any IO."""
    from app.api import dependencies as deps

    monkeypatch.setattr(deps, "db_engine", None, raising=False)
    monkeypatch.setattr(deps, "redis_pool", None, raising=False)

    await client.get("/readiness")  # warm
    t0 = time.perf_counter()
    resp = await client.get("/readiness")
    elapsed = time.perf_counter() - t0
    assert resp.status_code == 200
    assert elapsed < 0.25, f"/readiness took {elapsed:.3f}s; budget 0.25s"


# ---------------------------------------------------------------------------
# Concurrency on read-only endpoints
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_concurrent_health_requests_have_unique_trace_ids(client):
    results = await asyncio.gather(*(client.get("/health") for _ in range(20)))
    trace_ids = [r.headers.get("X-Trace-ID") for r in results]
    assert all(trace_ids), "Every concurrent request must carry a trace id"
    assert len(set(trace_ids)) == len(trace_ids), "Trace ids must be unique"


# ---------------------------------------------------------------------------
# Concurrency on /quiz/start
# ---------------------------------------------------------------------------

@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "turnstile_bypass",
    "override_redis_dep",
    "override_db_dependency",
)
async def test_concurrent_quiz_starts_get_distinct_quiz_ids(client):
    api = API_PREFIX.rstrip("/")
    payloads = [start_quiz_payload(topic=f"Topic-{i}") for i in range(8)]

    async def _call(p):
        return await client.post(f"{api}/quiz/start", json=p)

    responses = await asyncio.gather(*(_call(p) for p in payloads))
    assert all(r.status_code == 201 for r in responses), [
        (r.status_code, r.text[:200]) for r in responses
    ]
    quiz_ids = [r.json()["quizId"] for r in responses]

    # All UUIDs valid and unique
    for qid in quiz_ids:
        uuid.UUID(qid)
    assert len(set(quiz_ids)) == len(quiz_ids)


# ---------------------------------------------------------------------------
# Backoff helper
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("attempt", [1, 2, 5, 10, 100])
def test_jittered_backoff_is_bounded(attempt):
    delay = _jittered_backoff(attempt, base=0.05, cap=0.5)
    # Cap is 0.5 plus tiny jitter (<= 0.01 per implementation)
    assert 0.0 <= delay <= 0.51


def test_jittered_backoff_grows_then_caps():
    """Backoff should grow with attempt up to the cap."""
    d1 = _jittered_backoff(1, base=0.05, cap=10.0)
    d10 = _jittered_backoff(10, base=0.05, cap=10.0)
    # Allow some jitter but later attempt should be >= earlier when below cap.
    assert d10 >= d1 - 0.01


# ---------------------------------------------------------------------------
# Redis atomic update under WATCH conflicts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_quiz_state_gives_up_after_repeated_watch_conflicts():
    """When every WATCH attempt fires WatchError, return None within max_retries."""
    sid = uuid.uuid4()

    pipe = MagicMock()
    pipe.__aenter__ = AsyncMock(return_value=pipe)
    pipe.__aexit__ = AsyncMock(return_value=False)
    pipe.watch = AsyncMock()
    pipe.get = AsyncMock(return_value=None)

    # Each .get returns a non-None value so we proceed past the missing branch
    valid_state_json = (
        '{"session_id":"' + str(sid) + '",'
        '"category":"x","quiz_history":[],"messages":[],'
        '"generated_questions":[],"generated_characters":[],'
        '"final_result":null,"baseline_count":3,'
        '"ready_for_questions":false,"is_finished":false}'
    )
    pipe.get = AsyncMock(return_value=valid_state_json)
    # multi/set are sync per redis-py asyncio API
    pipe.multi = MagicMock()
    pipe.set = MagicMock()
    # execute always raises WatchError to simulate persistent conflicts
    pipe.execute = AsyncMock(side_effect=WatchError("conflict"))
    pipe.unwatch = AsyncMock()
    pipe.reset = MagicMock()

    client = MagicMock()
    client.pipeline = MagicMock(return_value=pipe)

    repo = CacheRepository(client)
    t0 = time.perf_counter()
    result = await repo.update_quiz_state_atomically(
        sid, {"category": "y"}, ttl_seconds=60
    )
    elapsed = time.perf_counter() - t0

    # Should give up after bounded retries -> None
    assert result is None
    # Total elapsed must remain bounded (max 8 retries * 0.5s cap + overhead)
    assert elapsed < 6.0, f"Atomic update did not bound retries: {elapsed:.2f}s"
