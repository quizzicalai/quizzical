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
# Blackbox #3 — MODEL + SIZE-aware per-image cost (no more flat $0.011).
# ---------------------------------------------------------------------------

def test_image_cost_model_size_aware():
    """A 1024px FLUX-dev hero costs ~$0.025; a 256px schnell thumb ~$0.0002."""
    from app.services.image_cost import image_cost_micros, image_cost_usd

    # FLUX dev 1024x1024 = 1.049 MP * $0.025/MP ~= $0.0262 (~$0.025).
    dev_hero = image_cost_usd(
        model="fal-ai/flux/dev", image_size={"width": 1024, "height": 1024}
    )
    assert dev_hero == pytest.approx(0.0262, abs=0.001)
    assert image_cost_micros(
        model="fal-ai/flux/dev", image_size={"width": 1024, "height": 1024}
    ) == 2621

    # FLUX schnell 256x256 = 0.0655 MP * $0.003/MP ~= $0.0002 (NOT $0.011).
    schnell_thumb = image_cost_usd(
        model="fal-ai/flux/schnell", image_size={"width": 256, "height": 256}
    )
    assert schnell_thumb == pytest.approx(0.0002, abs=0.0001)
    # Far below the old flat $0.011 — proves the cheap path no longer over-bills.
    assert schnell_thumb < 0.011

    # Unknown model falls back to the cheap schnell rate (never over-trips).
    assert image_cost_usd(
        model="totally-unknown", image_size={"width": 256, "height": 256}
    ) == pytest.approx(schnell_thumb, abs=1e-9)


@pytest.mark.asyncio
async def test_record_fal_image_cost_is_model_size_aware(monkeypatch):
    """A single FLUX-dev hero records ~3 cents; a single schnell thumb rounds to
    0 cents (sub-cent) in the integer daily counter."""
    recorded = {"cents": None}

    async def _fake_record(_redis, cents):
        recorded["cents"] = cents

    monkeypatch.setattr(cost_meter, "record_cents", _fake_record, raising=True)
    monkeypatch.setattr(cost_meter, "_get_redis_for_metering", lambda: object())

    # 1 dev hero @ 1024x1024 ~= $0.0262 -> 3 cents (nearest-cent).
    await cost_meter.record_fal_image_cost(
        1, model="fal-ai/flux/dev", image_size={"width": 1024, "height": 1024}
    )
    assert recorded["cents"] == 3

    # 1 schnell thumb @ 256px ~= $0.0002 -> rounds to 0 cents -> record_cents NOT
    # called (sub-cent images accrue only when a batch crosses a cent line).
    recorded["cents"] = None
    await cost_meter.record_fal_image_cost(
        1, model="fal-ai/flux/schnell", image_size={"width": 256, "height": 256}
    )
    assert recorded["cents"] is None  # sub-cent -> not recorded individually


# ---------------------------------------------------------------------------
# Punchlist #16 — reserve/reconcile edge paths (the money-path reconciliation
# behind /quiz/start's release-in-finally). These bound how the daily $ breaker
# counter converges to TRUE spend after the deliberately-conservative estimate
# reserved at admission is released. Bugs here either LEAK a reservation (breaker
# trips too early, blocking real users) or DOUBLE-COUNT / drive the counter
# negative (breaker mis-trips open, letting cost run away).
# ---------------------------------------------------------------------------


class _DecrRedis(_CountingRedis):
    """_CountingRedis + a real DECRBY, so we exercise the preferred DECR path."""

    async def decrby(self, key: str, amount: int) -> int:
        self.store[key] = self.store.get(key, 0) - int(amount)
        return self.store[key]


@pytest.mark.asyncio
async def test_reconcile_releases_exact_delta_when_actual_below_estimate():
    """actual < estimated -> DECRBY the over-reservation by EXACTLY the delta.

    This is the /quiz/start happy path: the per-call meter already accrued the
    real spend during the run, so the handler reconciles with actual=0 to release
    the whole estimate, leaving only the metered real cents."""
    r = _DecrRedis()
    # Simulate: reserve 20c at admission, real metered spend of 7c landed during
    # the run -> counter is 27. Release the full 20c estimate (actual=0).
    await cost_meter.reserve_estimated_cents(r, 20)
    await cost_meter.record_cents(r, 7)  # real per-call spend during the quiz
    assert r.store[cost_meter.daily_cents_key()] == 27

    total = await cost_meter.reconcile_reservation(
        r, estimated_cents=20, actual_cents=0
    )
    # Exactly the 20c estimate is removed; the 7c real spend remains.
    assert total == 7
    assert r.store[cost_meter.daily_cents_key()] == 7


@pytest.mark.asyncio
async def test_reconcile_adds_shortfall_when_actual_above_estimate():
    """actual > estimated -> INCRBY the shortfall (counter was under-reserved)."""
    r = _DecrRedis()
    await cost_meter.reserve_estimated_cents(r, 10)
    total = await cost_meter.reconcile_reservation(
        r, estimated_cents=10, actual_cents=15
    )
    assert total == 15  # +5 shortfall added on top of the 10c reservation


@pytest.mark.asyncio
async def test_reconcile_noop_when_actual_equals_estimate():
    r = _DecrRedis()
    await cost_meter.reserve_estimated_cents(r, 12)
    total = await cost_meter.reconcile_reservation(
        r, estimated_cents=12, actual_cents=12
    )
    assert total == 12  # unchanged; reconcile reads back the current total


@pytest.mark.asyncio
async def test_reconcile_preserves_concurrent_record_between_decr_and_reseat():
    """A concurrent ``record_cents`` (real LLM/FAL spend) that lands between the
    DECRBY returning and the negative-clamp reseat MUST be preserved, not clobbered.

    We drive the counter negative on purpose (release an estimate larger than the
    current total). DECRBY returns the negative value ``T`` it computed; a
    concurrent real-spend INCRBY of ``C`` then lands (making the true stored value
    ``T + C``) BEFORE the reseat runs. The reseat re-increments by ``-T`` (NOT a
    blind ``SET 0``), so the final value is ``T + C - T == C`` — the concurrent
    write survives. A blind SET(0) would have destroyed it."""
    key = cost_meter.daily_cents_key()

    class _RacingDecr(_CountingRedis):
        def __init__(self) -> None:
            super().__init__()
            self._armed = False  # only race the RESEAT incrby (post-DECRBY)
            self._raced = False

        async def decrby(self, k: str, amount: int) -> int:
            # DECRBY computes and returns T (the value AT decr time). After it
            # runs, arm the race so the NEXT (reseat) incrby sees a concurrent write.
            self.store[k] = self.store.get(k, 0) - int(amount)
            self._armed = True
            return self.store[k]

        async def incrby(self, k: str, amount: int) -> int:
            # The reseat uses incrby(key, -T). Simulate a concurrent real-spend
            # INCRBY of 9c landing just BEFORE this reseat increment applies.
            if self._armed and not self._raced and amount > 0:
                self._raced = True
                self.store[k] = self.store.get(k, 0) + 9  # concurrent 9c real spend
            return await super().incrby(k, amount)

    r = _RacingDecr()
    # Counter starts at 5c; release a 20c estimate -> DECRBY 20 -> T = -15. Then
    # the concurrent +9 lands (true value -6). The reseat incrby(-T = +15) yields
    # -6 + 15 = 9: the concurrent 9c is preserved and the counter is non-negative.
    await cost_meter.record_cents(r, 5)
    total = await cost_meter.reconcile_reservation(
        r, estimated_cents=20, actual_cents=0
    )
    # The 9c concurrent real spend survives the clamp; never negative.
    assert total == 9
    assert r.store[key] == 9


@pytest.mark.asyncio
async def test_reconcile_falls_back_to_negative_incrby_without_decrby():
    """A client/fake lacking DECRBY must still release via a negative INCRBY."""
    r = _CountingRedis()  # no .decrby attribute
    assert not hasattr(r, "decrby")
    await cost_meter.record_cents(r, 30)
    total = await cost_meter.reconcile_reservation(
        r, estimated_cents=20, actual_cents=0
    )
    assert total == 10  # 30 - 20 via the negative-INCRBY fallback path


@pytest.mark.asyncio
async def test_reconcile_clamps_at_zero_never_negative():
    """Releasing more than the counter holds clamps at 0 (a negative counter
    would mis-trip the breaker OPEN on the next read)."""
    r = _DecrRedis()
    await cost_meter.record_cents(r, 3)
    total = await cost_meter.reconcile_reservation(
        r, estimated_cents=20, actual_cents=0
    )
    assert total == 0
    assert r.store[cost_meter.daily_cents_key()] == 0


@pytest.mark.asyncio
async def test_reconcile_fails_open_on_none_client_and_redis_error():
    assert await cost_meter.reconcile_reservation(
        None, estimated_cents=5, actual_cents=0
    ) is None

    class _Bad:
        async def incrby(self, key, amount):
            raise RuntimeError("redis down")

        async def get(self, key):
            raise RuntimeError("redis down")

    # A fault reconciling must never raise into the /quiz/start finally block.
    assert await cost_meter.reconcile_reservation(
        _Bad(), estimated_cents=10, actual_cents=20
    ) is None


@pytest.mark.asyncio
async def test_reserve_estimated_cents_noop_on_nonpositive():
    r = _DecrRedis()
    assert await cost_meter.reserve_estimated_cents(r, 0) is None
    assert await cost_meter.reserve_estimated_cents(r, -4) is None
    assert r.store == {}
