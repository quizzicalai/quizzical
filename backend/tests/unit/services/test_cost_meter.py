"""Per-call token/$ capture + daily CENTS counter (Hitlist #2, 2026-06-30).

cost_meter records real LLM token/$ (via litellm.completion_cost) and FAL image
spend into a UTC-dated Redis cents counter that the live-cost breaker reads. The
hard contract is FAIL OPEN: a missing usage, an unmapped-pricing exception, or a
Redis fault must never raise — accounting is best-effort instrumentation.
"""
from __future__ import annotations

import pytest

from app.services import cost_meter


class _CountingRedis:
    """Minimal async Redis supporting incrby/expire/get on string-decoded values."""

    def __init__(self) -> None:
        self.store: dict[str, int] = {}
        self.expires: dict[str, int] = {}

    async def incrby(self, key: str, amount: int) -> int:
        self.store[key] = self.store.get(key, 0) + int(amount)
        return self.store[key]

    async def expire(self, key: str, ttl: int) -> bool:
        self.expires[key] = ttl
        return True

    async def get(self, key: str):
        v = self.store.get(key)
        return None if v is None else str(v)


@pytest.mark.asyncio
async def test_record_cents_increments_and_always_sets_ttl():
    """Review item C — the TTL is set on EVERY write (self-healing) so the key
    can never end up persistent. (Re-asserting a ~25h TTL on a UTC-dated key is
    idempotent.)"""
    r = _CountingRedis()
    key = cost_meter.daily_cents_key()

    total = await cost_meter.record_cents(r, 5)
    assert total == 5
    assert r.expires.get(key) == cost_meter._DAILY_TTL_S  # TTL set on first write

    r.expires.clear()
    total = await cost_meter.record_cents(r, 7)
    assert total == 12
    assert r.expires.get(key) == cost_meter._DAILY_TTL_S  # TTL re-asserted (self-heal)


@pytest.mark.asyncio
async def test_record_cents_uses_atomic_pipeline_when_available():
    """Review item C — when the client exposes a pipeline, INCRBY + EXPIRE go in
    one round-trip so the key can never persist TTL-less."""
    ops: list[tuple] = []

    class _Pipe:
        def __init__(self, store):
            self._store = store

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def incrby(self, key, amount):
            ops.append(("incrby", key, amount))
            self._store[key] = self._store.get(key, 0) + int(amount)

        def expire(self, key, ttl):
            ops.append(("expire", key, ttl))

        async def execute(self):
            key = ops[0][1]
            return [self._store[key], True]  # INCRBY result, EXPIRE result

    class _PipelineRedis:
        def __init__(self):
            self.store: dict[str, int] = {}

        def pipeline(self):
            return _Pipe(self.store)

    r = _PipelineRedis()
    total = await cost_meter.record_cents(r, 9)
    assert total == 9
    # Both ops were issued in the SAME pipeline, INCRBY before EXPIRE.
    assert [o[0] for o in ops] == ["incrby", "expire"]
    assert ops[1][2] == cost_meter._DAILY_TTL_S


@pytest.mark.asyncio
async def test_record_cents_self_heals_ttl_after_prior_expire_failure():
    """A key whose earlier EXPIRE failed gets its TTL re-asserted on the next
    metered write (fallback path)."""
    key = cost_meter.daily_cents_key()

    class _FlakyExpire(_CountingRedis):
        def __init__(self):
            super().__init__()
            self.fail_next_expire = True

        async def expire(self, key, ttl):
            if self.fail_next_expire:
                self.fail_next_expire = False
                raise RuntimeError("expire blip")
            return await super().expire(key, ttl)

    r = _FlakyExpire()
    await cost_meter.record_cents(r, 3)  # INCRBY ok, EXPIRE fails -> no TTL yet
    assert key not in r.expires
    await cost_meter.record_cents(r, 4)  # next write re-asserts the TTL
    assert r.expires.get(key) == cost_meter._DAILY_TTL_S


@pytest.mark.asyncio
async def test_record_cents_noop_on_nonpositive_or_none_client():
    r = _CountingRedis()
    assert await cost_meter.record_cents(r, 0) is None
    assert await cost_meter.record_cents(r, -3) is None
    assert await cost_meter.record_cents(None, 5) is None
    assert r.store == {}


@pytest.mark.asyncio
async def test_record_cents_fails_open_on_redis_error():
    class _Bad:
        async def incrby(self, key, amount):
            raise RuntimeError("redis down")

    # Must not raise.
    assert await cost_meter.record_cents(_Bad(), 5) is None


@pytest.mark.asyncio
async def test_read_daily_cents_zero_then_value_then_fail_open():
    r = _CountingRedis()
    assert await cost_meter.read_daily_cents(r) == 0  # missing key -> 0
    await cost_meter.record_cents(r, 42)
    assert await cost_meter.read_daily_cents(r) == 42

    class _Bad:
        async def get(self, key):
            raise RuntimeError("boom")

    assert await cost_meter.read_daily_cents(_Bad()) is None  # fail-open
    assert await cost_meter.read_daily_cents(None) is None


def test_extract_usage_responses_and_chat_shapes():
    # Responses API shape (input/output tokens).
    u = cost_meter._extract_usage({"usage": {"input_tokens": 10, "output_tokens": 3}})
    assert u["input_tokens"] == 10 and u["output_tokens"] == 3 and u["total_tokens"] == 13
    # Chat Completions shape (prompt/completion tokens).
    u2 = cost_meter._extract_usage(
        {"usage": {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10}}
    )
    assert u2["input_tokens"] == 4 and u2["output_tokens"] == 6 and u2["total_tokens"] == 10
    # No usage -> all zero (never raises).
    assert cost_meter._extract_usage({}) == {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }


def test_usd_to_cents_rounds_nearest_and_is_conservative():
    assert cost_meter._usd_to_cents(0.011) == 1  # ~1 cent
    assert cost_meter._usd_to_cents(0.006) == 1  # rounds up across the cent line
    assert cost_meter._usd_to_cents(0.004) == 0
    assert cost_meter._usd_to_cents(0.50) == 50


@pytest.mark.asyncio
async def test_record_llm_cost_fails_open_when_completion_cost_raises(monkeypatch):
    """An unmapped-model litellm.completion_cost exception must not propagate and
    must record 0 cents (no Redis call needed)."""
    import litellm

    def _boom(*_a, **_k):
        raise RuntimeError("This model isn't mapped yet.")

    monkeypatch.setattr(litellm, "completion_cost", _boom, raising=True)

    captured = {"called": False}

    async def _fake_record(_redis, _cents):
        captured["called"] = True

    monkeypatch.setattr(cost_meter, "record_cents", _fake_record, raising=True)

    # Must not raise; 0 cents -> record_cents NOT called.
    await cost_meter.record_llm_cost(
        {"usage": {"input_tokens": 5, "output_tokens": 2}},
        model="some/unknown-model",
        tool="t",
        trace_id="tr",
        session_id="s",
    )
    assert captured["called"] is False


@pytest.mark.asyncio
async def test_record_llm_cost_records_cents_when_cost_known(monkeypatch):
    import litellm

    monkeypatch.setattr(litellm, "completion_cost", lambda **_k: 0.02, raising=True)

    recorded = {}

    async def _fake_record(_redis, cents):
        recorded["cents"] = cents

    monkeypatch.setattr(cost_meter, "record_cents", _fake_record, raising=True)
    monkeypatch.setattr(cost_meter, "_get_redis_for_metering", lambda: object())

    await cost_meter.record_llm_cost(
        {"usage": {"input_tokens": 5, "output_tokens": 2}},
        model="gpt-4o-mini",
        tool="next_question_generator",
        trace_id="tr",
        session_id="s",
    )
    assert recorded["cents"] == 2  # $0.02 -> 2 cents


@pytest.mark.asyncio
async def test_record_fal_image_cost_uses_config_per_image(monkeypatch):
    cfg = cost_meter._live_cost_cfg()
    monkeypatch.setattr(cfg, "fal_image_cost_usd", 0.011, raising=False)

    recorded = {}

    async def _fake_record(_redis, cents):
        recorded["cents"] = cents

    monkeypatch.setattr(cost_meter, "record_cents", _fake_record, raising=True)
    monkeypatch.setattr(cost_meter, "_get_redis_for_metering", lambda: object())

    await cost_meter.record_fal_image_cost(10)  # 10 * $0.011 = $0.11 = 11 cents
    assert recorded["cents"] == 11

    recorded.clear()
    await cost_meter.record_fal_image_cost(0)  # no-op
    assert recorded == {}


@pytest.mark.asyncio
async def test_record_fal_image_cost_fails_open(monkeypatch):
    async def _boom(*_a, **_k):
        raise RuntimeError("redis down")

    monkeypatch.setattr(cost_meter, "record_cents", _boom, raising=True)
    monkeypatch.setattr(cost_meter, "_get_redis_for_metering", lambda: object())
    # Must not raise.
    await cost_meter.record_fal_image_cost(3)


# ---------------------------------------------------------------------------
# Hitlist #1 (2026-06-30) — admission reservation + reconcile.
# ---------------------------------------------------------------------------


class _FullRedis(_CountingRedis):
    """Adds decrby + set so reconcile can release / re-seat the counter."""

    async def decrby(self, key: str, amount: int) -> int:
        self.store[key] = self.store.get(key, 0) - int(amount)
        return self.store[key]

    async def set(self, key: str, value) -> bool:
        self.store[key] = int(value)
        return True


@pytest.mark.asyncio
async def test_reserve_estimated_cents_increments_and_is_visible_to_reads():
    """A reservation lands on the SAME daily counter the breaker reads, so a
    concurrent admission sees the prior reservation (soft -> near-hard)."""
    r = _FullRedis()
    total = await cost_meter.reserve_estimated_cents(r, 5)
    assert total == 5
    # The breaker's read sees the reservation immediately.
    assert await cost_meter.read_daily_cents(r) == 5
    # A second concurrent admission stacks on top.
    total2 = await cost_meter.reserve_estimated_cents(r, 5)
    assert total2 == 10


@pytest.mark.asyncio
async def test_reserve_estimated_cents_noop_and_fail_open():
    r = _FullRedis()
    assert await cost_meter.reserve_estimated_cents(r, 0) is None
    assert await cost_meter.reserve_estimated_cents(r, -3) is None
    assert r.store == {}

    class _Bad:
        async def incrby(self, key, amount):
            raise RuntimeError("redis down")

    # Fail-open: a reservation fault must never raise.
    assert await cost_meter.reserve_estimated_cents(_Bad(), 5) is None


@pytest.mark.asyncio
async def test_reconcile_applies_signed_delta_actual_minus_estimated():
    """reconcile adjusts the counter by the SIGNED delta ``actual - estimated``.

    This test exercises the general arithmetic with actual(2) < estimated(5):
    delta = 2 - 5 = -3, so the counter drops by 3. (The endpoints always call
    with ``actual=0`` to release the whole estimate — see the next test — but the
    function supports any signed reconcile.)"""
    r = _FullRedis()
    # Reserve 5 at admission, then the meter accrues 2 real cents during the run.
    await cost_meter.reserve_estimated_cents(r, 5)
    await cost_meter.record_cents(r, 2)
    assert await cost_meter.read_daily_cents(r) == 7
    # delta = actual(2) - estimated(5) = -3  ->  7 - 3 = 4.
    total = await cost_meter.reconcile_reservation(r, estimated_cents=5, actual_cents=2)
    assert total == 4


@pytest.mark.asyncio
async def test_reconcile_actual_zero_releases_whole_reservation():
    r = _FullRedis()
    await cost_meter.reserve_estimated_cents(r, 5)
    await cost_meter.record_cents(r, 2)  # metered real spend
    assert await cost_meter.read_daily_cents(r) == 7
    # The real release path used by the endpoints: actual=0 removes the estimate,
    # leaving only the metered real spend.
    total = await cost_meter.reconcile_reservation(r, estimated_cents=5, actual_cents=0)
    assert total == 2


@pytest.mark.asyncio
async def test_reconcile_under_reservation_increments():
    r = _FullRedis()
    await cost_meter.reserve_estimated_cents(r, 5)
    # Actual exceeded the estimate -> reconcile adds the shortfall.
    total = await cost_meter.reconcile_reservation(r, estimated_cents=5, actual_cents=8)
    assert total == 8


@pytest.mark.asyncio
async def test_reconcile_never_drives_counter_negative():
    r = _FullRedis()
    # Counter is small; releasing a large estimate must clamp at 0, never go
    # negative (a negative read would mis-trip the breaker OPEN).
    await cost_meter.reserve_estimated_cents(r, 2)
    total = await cost_meter.reconcile_reservation(r, estimated_cents=10, actual_cents=0)
    assert total == 0
    assert await cost_meter.read_daily_cents(r) == 0


@pytest.mark.asyncio
async def test_reconcile_negative_clamp_preserves_concurrent_spend():
    """Review LOW fix — the negative-clamp must be ATOMIC. A concurrent
    ``record_cents`` INCRBY (real LLM/FAL spend) landing between our DECRBY and
    the clamp must be PRESERVED, not clobbered to 0 by a blind SET.

    We model the race with a Redis fake that injects a concurrent +5 real-spend
    INCRBY at the moment the CLAMP's ``incrby`` runs (i.e. after DECRBY returned
    the negative total, while reconcile is reseating the counter). The atomic
    clamp adds ``-total`` to whatever the live value is, so the concurrent +5
    survives. A blind ``SET(key, 0)`` (the old code) would have ignored that live
    value and wiped the +5 to 0 — which this test would catch."""

    class _RacingClampRedis(_FullRedis):
        def __init__(self) -> None:
            super().__init__()
            self._seen_decrby = False

        async def decrby(self, key: str, amount: int) -> int:
            self._seen_decrby = True
            self.store[key] = self.store.get(key, 0) - int(amount)
            return self.store[key]

        async def incrby(self, key: str, amount: int) -> int:
            # This INCRBY is the CLAMP re-increment (it only runs after a DECRBY).
            # Inject a concurrent real-spend +5 right before applying the clamp,
            # to prove the clamp ADDS to the live value (preserving the +5)
            # rather than SETting a blind 0.
            if self._seen_decrby:
                self.store[key] = self.store.get(key, 0) + 5  # concurrent real spend
                self._seen_decrby = False
            return await super().incrby(key, amount)

    r = _RacingClampRedis()
    await cost_meter.reserve_estimated_cents(r, 2)  # counter = 2
    # Release a 10-cent estimate: DECRBY 10 -> 2-10 = -8 (negative -> clamp).
    # During the clamp: concurrent +5 -> -3, then clamp incrby(-(-8)=+8) -> +5.
    # The concurrent +5 SURVIVES (a blind SET(0) would have produced 0).
    total = await cost_meter.reconcile_reservation(r, estimated_cents=10, actual_cents=0)
    assert total == 5
    assert await cost_meter.read_daily_cents(r) == 5


@pytest.mark.asyncio
async def test_reconcile_negative_clamp_keeps_positive_concurrent_overspend():
    """If a LARGE concurrent INCRBY makes the counter positive again during the
    clamp window, the clamp must KEEP that positive value (the real spend), not
    force it to 0."""

    class _BigRaceRedis(_FullRedis):
        async def decrby(self, key: str, amount: int) -> int:
            self.store[key] = self.store.get(key, 0) - int(amount)  # goes negative
            self.store[key] = self.store.get(key, 0) + 50  # big concurrent real spend
            return self.store[key]

    r = _BigRaceRedis()
    await cost_meter.reserve_estimated_cents(r, 2)  # counter = 2
    # DECRBY 10 -> -8, then +50 -> 42. total>0 so the function returns it directly
    # (no clamp needed), preserving the 42 of concurrent real spend.
    total = await cost_meter.reconcile_reservation(r, estimated_cents=10, actual_cents=0)
    assert total == 42
    assert await cost_meter.read_daily_cents(r) == 42


@pytest.mark.asyncio
async def test_reconcile_noop_when_equal_and_fail_open():
    r = _FullRedis()
    await cost_meter.reserve_estimated_cents(r, 5)
    # Equal estimate/actual -> no adjustment, returns current total.
    total = await cost_meter.reconcile_reservation(r, estimated_cents=5, actual_cents=5)
    assert total == 5
    assert await cost_meter.reconcile_reservation(None, estimated_cents=5, actual_cents=0) is None

    class _Bad:
        async def incrby(self, key, amount):
            raise RuntimeError("redis down")

        async def decrby(self, key, amount):
            raise RuntimeError("redis down")

    # Fail-open on a fault.
    assert (
        await cost_meter.reconcile_reservation(_Bad(), estimated_cents=5, actual_cents=0)
        is None
    )
