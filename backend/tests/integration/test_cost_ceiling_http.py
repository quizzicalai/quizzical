"""HTTP-level enforcement of the daily cost ceiling + session action cap
(punchlist #17).

Every existing breaker test calls the helpers (``_enforce_global_daily_cost_ceiling``
/ ``_enforce_session_action_cap``) DIRECTLY. None proves the paid endpoints
actually WIRE those helpers into the request path — a refactor that dropped the
``await _enforce_...`` line would pass every unit test while removing the money
guard in production.

These tests drive the real ASGI app (existing async_client + fake-redis
fixtures), seed the daily cents counter OVER budget, and assert:

  * ``POST /quiz/start``   -> 503 ``QF_COST_CEILING``
  * ``POST /quiz/proceed`` -> 503 ``QF_COST_CEILING``
  * ``POST /quiz/next``    -> 503 ``QF_COST_CEILING``
  * ``POST /quiz/next`` past the per-session action cap -> 429 ``QF_SESSION_ACTION_CAP``

Tests only — no source change to quiz.py.
"""
from __future__ import annotations

import uuid

import pytest

from app.api.endpoints import quiz as quiz_module
from app.core.error_codes import QF_COST_CEILING, QF_SESSION_ACTION_CAP
from app.main import API_PREFIX
from app.services.cost_meter import daily_cents_key
from tests.fixtures.redis_fixtures import seed_quiz_state
from tests.helpers.sample_payloads import (
    next_question_payload,
    proceed_payload,
    start_quiz_payload,
)
from tests.helpers.state_builders import make_questions_state

_API = API_PREFIX.rstrip("/")


def _seed_over_budget(fake_redis) -> None:
    """Seed the UTC-dated daily cents counter far above any configured budget so
    the dollar breaker's read-check trips. ``read_daily_cents`` reads this key via
    ``redis.get`` (which the fake supports)."""
    # 10_000_000 cents == $100_000, above any sane ``daily_budget_usd``.
    fake_redis._kv[daily_cents_key()] = "10000000"


@pytest.fixture
def _cost_guard_on(monkeypatch):
    """Force the live-cost guard ON with a tiny budget so the seeded counter is
    unambiguously over the ceiling regardless of local config."""
    cfg = quiz_module.settings.security.live_cost_guard
    monkeypatch.setattr(cfg, "enabled", True, raising=False)
    monkeypatch.setattr(cfg, "daily_budget_usd", 1.0, raising=False)  # $1 == 100c
    # Isolate the DOLLAR breaker from the secondary start-count backstop.
    monkeypatch.setattr(cfg, "max_quiz_starts_per_day", 0, raising=False)
    return cfg


@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "turnstile_bypass",
    "override_redis_dep",
    "override_db_dependency",
    "_cost_guard_on",
)
async def test_quiz_start_trips_cost_ceiling_503(client, fake_redis):
    _seed_over_budget(fake_redis)
    resp = await client.post(
        f"{_API}/quiz/start?_a=test&_k=test",
        json=start_quiz_payload(topic="Astronomy"),
    )
    assert resp.status_code == 503, resp.text
    body = resp.json()
    # Whimsical coded-error envelope carries the QF code.
    assert QF_COST_CEILING in resp.text or body.get("qfCode") == QF_COST_CEILING


@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "turnstile_bypass",
    "override_redis_dep",
    "override_db_dependency",
    "_cost_guard_on",
)
async def test_quiz_proceed_trips_cost_ceiling_503(client, fake_redis):
    quiz_id = uuid.uuid4()
    # /proceed checks the session exists before the cost gate — seed a state.
    seed_quiz_state(
        fake_redis,
        quiz_id,
        make_questions_state(quiz_id=quiz_id, category="Astronomy"),
    )
    _seed_over_budget(fake_redis)

    resp = await client.post(
        f"{_API}/quiz/proceed",
        json=proceed_payload(quiz_id),
    )
    assert resp.status_code == 503, resp.text
    assert QF_COST_CEILING in resp.text


@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "turnstile_bypass",
    "override_redis_dep",
    "override_db_dependency",
    "_cost_guard_on",
)
async def test_quiz_next_trips_cost_ceiling_503(client, fake_redis):
    quiz_id = uuid.uuid4()
    seed_quiz_state(
        fake_redis,
        quiz_id,
        make_questions_state(
            quiz_id=quiz_id,
            category="Astronomy",
            questions=["What orbits the sun?", "What is a light-year?"],
            baseline_count=2,
            answers=[],
        ),
    )
    _seed_over_budget(fake_redis)

    resp = await client.post(
        f"{_API}/quiz/next",
        json=next_question_payload(quiz_id, index=0, option_idx=0),
    )
    assert resp.status_code == 503, resp.text
    assert QF_COST_CEILING in resp.text


# ---------------------------------------------------------------------------
# Per-session action cap (429) — HTTP level. The cap uses redis.incr, so we give
# this test a fake redis that supports incr/expire (the default fake does not,
# which would fail the cap OPEN). We pre-seed the action counter at the cap so a
# single /quiz/next crosses it, and DISABLE the dollar breaker so we isolate the
# 429 (the action cap is enforced BEFORE the cost gate).
# ---------------------------------------------------------------------------
class _IncrRedis:
    """Minimal async fake supporting the ops /quiz/next touches: get/set/delete/
    pipeline (for state) plus incr/incrby/expire (for the action cap)."""

    def __init__(self, backing) -> None:
        self._backing = backing  # reuse the _FakeRedis for state get/set/pipeline

    # --- delegate state ops to the wrapped _FakeRedis -----------------------
    async def get(self, key):
        return await self._backing.get(key)

    async def set(self, *a, **k):
        return await self._backing.set(*a, **k)

    async def delete(self, *keys):
        return await self._backing.delete(*keys)

    def pipeline(self):
        return self._backing.pipeline()

    # --- counter ops for the action cap -------------------------------------
    async def incr(self, key):
        cur = int(self._backing._kv.get(key, 0)) + 1
        self._backing._kv[key] = cur
        return cur

    async def incrby(self, key, amount):
        cur = int(self._backing._kv.get(key, 0)) + int(amount)
        self._backing._kv[key] = cur
        return cur

    async def expire(self, key, ttl):
        return True


@pytest.mark.anyio
@pytest.mark.usefixtures(
    "use_fake_agent_graph",
    "turnstile_bypass",
    "override_db_dependency",
)
async def test_quiz_next_trips_session_action_cap_429(monkeypatch, fake_redis):
    from app.api.dependencies import get_redis_client
    from app.main import app as fastapi_app

    # Small cap so a single /next crosses it: cap = max_total_questions + 10.
    monkeypatch.setattr(
        quiz_module.settings.quiz, "max_total_questions", 1, raising=False
    )  # cap == 11
    # Disable the dollar breaker so the 429 (cap) is what we observe, not a 503.
    monkeypatch.setattr(
        quiz_module.settings.security.live_cost_guard, "enabled", False, raising=False
    )

    quiz_id = uuid.uuid4()
    seed_quiz_state(
        fake_redis,
        quiz_id,
        make_questions_state(
            quiz_id=quiz_id,
            category="Astronomy",
            questions=["Q1?", "Q2?"],
            baseline_count=2,
            answers=[],
        ),
    )
    # Pre-seed the action counter AT the cap (11) so the next incr -> 12 > cap.
    fake_redis._kv[f"quiz_actions:{quiz_id}"] = 11

    incr_redis = _IncrRedis(fake_redis)

    async def _dep():
        return incr_redis

    fastapi_app.dependency_overrides[get_redis_client] = _dep
    try:
        resp = await client_post_next(quiz_id)
    finally:
        fastapi_app.dependency_overrides.pop(get_redis_client, None)

    assert resp.status_code == 429, resp.text
    assert QF_SESSION_ACTION_CAP in resp.text


# Small helper to make an ASGI request without depending on the `client` fixture
# (which pins override_redis_dep to the default fake). We build a client bound to
# the app with the current dependency_overrides in place.
async def client_post_next(quiz_id):
    import inspect

    from httpx import ASGITransport, AsyncClient

    from app.main import app as fastapi_app

    params = inspect.signature(ASGITransport.__init__).parameters
    if "lifespan" in params:
        transport = ASGITransport(app=fastapi_app, lifespan="auto")
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            return await c.post(
                f"{_API}/quiz/next",
                json=next_question_payload(quiz_id, index=0, option_idx=0),
            )
    async with fastapi_app.router.lifespan_context(fastapi_app):
        transport = ASGITransport(app=fastapi_app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            return await c.post(
                f"{_API}/quiz/next",
                json=next_question_payload(quiz_id, index=0, option_idx=0),
            )
