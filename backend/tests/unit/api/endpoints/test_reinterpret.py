# backend/tests/unit/api/endpoints/test_reinterpret.py
"""
"Try a different interpretation" (owner blackbox, 2026-07-02) — /quiz/start
reinterpret support.

Covers:
- rejected interpretations are threaded into the agent's initial state (and
  the key is ABSENT for a normal start, keeping that path byte-for-byte);
- the precompute short-circuit is bypassed for a reinterpret — and ONLY then;
- the per-chain cap returns a clear 429 (QF-REINTERPRET-CAP) via both layers:
  the deterministic rejected-list length check and the per-(IP, topic) Redis
  chain counter;
- cost/abuse parity: a reinterpret runs through the exact same Turnstile and
  per-IP /quiz/start throttle gates as a normal start;
- the rejection chain round-trips through the Redis state cache (the
  AgentGraphStateModel mirror must not drop it — state-consistency guard).
"""

import json

import pytest

from app.core.config import settings
from app.main import API_PREFIX

# Fixtures
from tests.fixtures.agent_graph_fixtures import use_fake_agent_graph  # noqa: F401
from tests.fixtures.db_fixtures import override_db_dependency  # noqa: F401
from tests.fixtures.redis_fixtures import (  # noqa: F401
    fake_cache_store,
    fake_redis,
    override_redis_dep,
)
from tests.fixtures.turnstile_fixtures import turnstile_bypass  # noqa: F401

# Helpers
from tests.helpers.sample_payloads import start_quiz_payload

api = API_PREFIX.rstrip("/")
pytestmark = pytest.mark.anyio

REJECTED_ONE = [
    "Quiz: Trolls — A quiz about the grumpy bridge-dwelling trolls of folklore."
]


def reinterpret_payload(topic: str = "Trolls", rejected: list[str] | None = None) -> dict:
    """A /quiz/start payload carrying prior rejected interpretations."""
    payload = start_quiz_payload(topic=topic)
    payload["rejectedInterpretations"] = list(
        REJECTED_ONE if rejected is None else rejected
    )
    return payload


def _install_ainvoke_spy(monkeypatch) -> dict:
    """Capture the initial state /quiz/start passes to the agent graph."""
    import app.main as main_mod

    graph = main_mod.app.state.agent_graph
    orig = graph.ainvoke
    captured: dict = {}

    async def _spy(state, config):
        captured.setdefault("states", []).append(dict(state))
        return await orig(state, config)

    monkeypatch.setattr(graph, "ainvoke", _spy, raising=True)
    return captured


def _give_fake_redis_counters(fake_redis) -> None:
    """The shared _FakeRedis lacks incr/expire (best-effort counters fail open).
    Attach real implementations on THIS instance so the chain counter engages."""

    async def _incr(key: str) -> int:
        value = int(fake_redis._kv.get(key, 0)) + 1
        fake_redis._kv[key] = str(value)
        return value

    async def _expire(key: str, ttl: int) -> bool:
        return True

    fake_redis.incr = _incr
    fake_redis.expire = _expire


# ---------------------------------------------------------------------------
# Planner threading
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures(
    "use_fake_agent_graph", "override_redis_dep", "turnstile_bypass", "override_db_dependency"
)
async def test_reinterpret_threads_rejected_into_agent_state(async_client, monkeypatch):
    captured = _install_ainvoke_spy(monkeypatch)

    response = await async_client.post(
        f"{api}/quiz/start", json=reinterpret_payload()
    )

    assert response.status_code == 201, response.text
    states = captured["states"]
    assert states, "agent graph was never invoked"
    assert states[0].get("rejected_interpretations") == REJECTED_ONE
    # Same-topic reinterpret still starts a brand-new quiz.
    assert response.json()["quizId"]


@pytest.mark.usefixtures(
    "use_fake_agent_graph", "override_redis_dep", "turnstile_bypass", "override_db_dependency"
)
async def test_normal_start_state_has_no_rejected_key(async_client, monkeypatch):
    """A normal start must be byte-for-byte unchanged: no new state key."""
    captured = _install_ainvoke_spy(monkeypatch)

    response = await async_client.post(
        f"{api}/quiz/start", json=start_quiz_payload(topic="Trolls")
    )

    assert response.status_code == 201, response.text
    assert "rejected_interpretations" not in captured["states"][0]


@pytest.mark.usefixtures(
    "use_fake_agent_graph", "override_redis_dep", "turnstile_bypass", "override_db_dependency"
)
async def test_rejected_chain_round_trips_through_redis(async_client, fake_cache_store):
    """The rejection chain must survive the Redis save (AgentGraphStateModel
    mirrors the GraphState field — see tests/agent_modernization/
    test_state_consistency.py): a mid-quiz rehydrate must not silently drop it.
    Also proves save_quiz_state's validation accepted the field (a validation
    failure is swallowed and would leave no key at all)."""
    response = await async_client.post(f"{api}/quiz/start", json=reinterpret_payload())
    assert response.status_code == 201, response.text
    quiz_id = response.json()["quizId"]

    raw = fake_cache_store.get(f"quiz_session:{quiz_id}")
    assert raw, "quiz state was not saved to Redis"
    stored = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
    assert stored["rejected_interpretations"] == REJECTED_ONE

    # Model-level round-trip: validate + dump preserves the chain verbatim.
    from app.agent.schemas import AgentGraphStateModel

    model = AgentGraphStateModel.model_validate(stored)
    assert model.rejected_interpretations == REJECTED_ONE
    assert model.model_dump()["rejected_interpretations"] == REJECTED_ONE


# ---------------------------------------------------------------------------
# Precompute bypass
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures(
    "use_fake_agent_graph", "override_redis_dep", "turnstile_bypass", "override_db_dependency"
)
async def test_reinterpret_bypasses_precompute_lookup(async_client, monkeypatch):
    """With precompute enabled, a reinterpret must NOT consult the resolver
    (a precomputed pack would serve back the interpretation the user just
    rejected) — while a normal start still does."""
    import app.services.precompute.lookup as lookup_mod

    monkeypatch.setattr(settings.precompute, "enabled", True)

    calls: list[str] = []

    async def _spy_resolve(self, category: str):
        calls.append(category)
        return None  # miss -> normal starts fall through to the live agent

    monkeypatch.setattr(lookup_mod.PrecomputeLookup, "resolve_topic", _spy_resolve)

    # Reinterpret: resolver must be skipped entirely.
    r1 = await async_client.post(f"{api}/quiz/start", json=reinterpret_payload())
    assert r1.status_code == 201, r1.text
    assert calls == []

    # Normal start of the same topic: resolver runs (only reinterprets bypass).
    r2 = await async_client.post(
        f"{api}/quiz/start", json=start_quiz_payload(topic="Trolls")
    )
    assert r2.status_code == 201, r2.text
    assert calls == ["Trolls"]


# ---------------------------------------------------------------------------
# Chain cap (429)
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures(
    "use_fake_agent_graph", "override_redis_dep", "turnstile_bypass", "override_db_dependency"
)
async def test_reinterpret_cap_rejected_list_length_429(async_client):
    """Deterministic layer: a rejected list longer than the cap is a clear 429
    with the dedicated QF-REINTERPRET-CAP code and a Retry-After header."""
    cap = int(settings.quiz.max_reinterprets_per_chain)
    over_cap = [f"Quiz: Trolls — reading number {i}" for i in range(cap + 1)]

    response = await async_client.post(
        f"{api}/quiz/start", json=reinterpret_payload(rejected=over_cap)
    )

    assert response.status_code == 429, response.text
    body = response.json()
    assert body["code"] == "QF-REINTERPRET-CAP"
    assert response.headers.get("Retry-After")


@pytest.mark.usefixtures(
    "use_fake_agent_graph", "override_redis_dep", "turnstile_bypass", "override_db_dependency"
)
async def test_reinterpret_cap_chain_counter_429(async_client, fake_redis):
    """Server-side layer: the per-(IP, topic) Redis counter bounds the chain
    even when each request carries a short rejected list (a client replaying
    one rejected entry cannot cycle forever)."""
    _give_fake_redis_counters(fake_redis)
    cap = int(settings.quiz.max_reinterprets_per_chain)

    for i in range(cap):
        r = await async_client.post(f"{api}/quiz/start", json=reinterpret_payload())
        assert r.status_code == 201, f"reinterpret {i + 1}/{cap} failed: {r.text}"

    over = await async_client.post(f"{api}/quiz/start", json=reinterpret_payload())
    assert over.status_code == 429, over.text
    assert over.json()["code"] == "QF-REINTERPRET-CAP"

    # A DIFFERENT topic is a fresh chain — not blocked by the tripped counter.
    other = await async_client.post(
        f"{api}/quiz/start",
        json=reinterpret_payload(topic="Wizards", rejected=["Quiz: Wizards — generic wizards"]),
    )
    assert other.status_code == 201, other.text


@pytest.mark.usefixtures(
    "use_fake_agent_graph", "override_redis_dep", "turnstile_bypass", "override_db_dependency"
)
async def test_normal_start_not_blocked_by_tripped_chain_counter(async_client, fake_redis):
    """The cap gates REINTERPRETS only: after the chain trips, a normal start
    of the same topic (empty rejected list) still succeeds."""
    _give_fake_redis_counters(fake_redis)
    cap = int(settings.quiz.max_reinterprets_per_chain)

    for _ in range(cap):
        r = await async_client.post(f"{api}/quiz/start", json=reinterpret_payload())
        assert r.status_code == 201, r.text
    over = await async_client.post(f"{api}/quiz/start", json=reinterpret_payload())
    assert over.status_code == 429

    normal = await async_client.post(
        f"{api}/quiz/start", json=start_quiz_payload(topic="Trolls")
    )
    assert normal.status_code == 201, normal.text


# ---------------------------------------------------------------------------
# Turnstile / rate-limit parity
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures(
    "use_fake_agent_graph", "override_redis_dep", "override_db_dependency"
)
async def test_reinterpret_requires_turnstile_token(async_client, monkeypatch):
    """Parity: a reinterpret is a PAID agent run and passes through the exact
    same verify_turnstile dependency as a normal start — no token, no run."""
    # Enable the global security toggle (ENABLE_TURNSTILE mirrors this flag).
    monkeypatch.setattr(settings.security, "enabled", True)

    payload = reinterpret_payload()
    payload.pop("cf-turnstile-response", None)

    response = await async_client.post(f"{api}/quiz/start", json=payload)

    assert response.status_code == 400, response.text
    assert response.json()["code"] == "QF-TURNSTILE-MISSING"


@pytest.mark.usefixtures(
    "use_fake_agent_graph", "override_redis_dep", "turnstile_bypass", "override_db_dependency"
)
async def test_reinterpret_hits_quiz_start_ip_throttle(async_client, fake_redis):
    """Parity: the per-IP /quiz/start token bucket evaluates reinterprets too.
    Simulate an exhausted START bucket via the limiter's Lua eval hook (the
    app-wide middleware bucket stays open so the request reaches the start
    throttle) and assert the reinterpret is rejected with its own 429 code."""

    async def _eval(script, numkeys, key, *args):
        if str(key).startswith("rl:quiz_start:"):
            return [0, 0, 30]  # allowed=0, remaining=0, retry_after=30s
        return [1, 10, 0]  # any other bucket (global middleware): allowed

    fake_redis.eval = _eval

    response = await async_client.post(f"{api}/quiz/start", json=reinterpret_payload())

    assert response.status_code == 429, response.text
    assert response.json()["code"] == "QF-QUIZ-START-RATE-LIMITED"
