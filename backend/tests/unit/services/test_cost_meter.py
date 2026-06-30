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
