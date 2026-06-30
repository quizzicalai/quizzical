"""Resend support-notifier (owner request, 2026-06-30).

Verifies the hard contract:
  * no RESEND_API_KEY  -> graceful no-op + WARNING, never raises, never sends;
  * rate-limit + dedupe by code (≤1 send per code per window) via Redis NX;
  * fail-open: Redis fault / no Redis -> SKIP send (never an email storm);
  * fire-and-forget never blocks / raises into the caller;
  * payload carries code + trace_id + NON-PII context only.
"""

from __future__ import annotations

import asyncio

import pytest

import app.core.error_codes as ec
import app.services.support_notify as sn

pytestmark = pytest.mark.anyio


class _FakeRedis:
    """Minimal Redis supporting SET key val NX EX."""

    def __init__(self) -> None:
        self._kv: dict[str, str] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self._kv:
            return None  # real redis-py returns None on NX miss
        self._kv[key] = value
        return True


class _BoomRedis:
    async def set(self, *a, **k):
        raise RuntimeError("redis down")


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    # Default: key absent unless a test sets it.
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("RESEND_FROM", raising=False)
    monkeypatch.delenv("SUPPORT_NOTIFY_TO", raising=False)


# ---------------------------------------------------------------------------
# No-key no-op
# ---------------------------------------------------------------------------

async def test_no_key_is_graceful_noop(monkeypatch) -> None:
    """Without RESEND_API_KEY the notifier must no-op (never POST, never raise)."""
    posted: list = []

    async def _post(api_key, payload):
        posted.append(payload)

    monkeypatch.setattr(sn, "_post_to_resend", _post)
    monkeypatch.setattr(sn, "_get_redis", lambda: _FakeRedis())

    spec = ec.get_spec(ec.QF_LLM_PROVIDER_DOWN)
    await sn._notify_async(spec, trace_id="t-1", path="/x", context=None)
    assert posted == []  # no send without a key


# ---------------------------------------------------------------------------
# Rate-limit / dedupe
# ---------------------------------------------------------------------------

async def test_dedupe_allows_one_then_blocks(monkeypatch) -> None:
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    redis = _FakeRedis()
    monkeypatch.setattr(sn, "_get_redis", lambda: redis)

    posted: list = []

    async def _post(api_key, payload):
        posted.append(payload)

    monkeypatch.setattr(sn, "_post_to_resend", _post)

    spec = ec.get_spec(ec.QF_COST_CEILING)
    # First two fire-bursts for the SAME code: only the first sends.
    await sn._notify_async(spec, trace_id="t-1", path="/x", context=None)
    await sn._notify_async(spec, trace_id="t-2", path="/x", context=None)
    assert len(posted) == 1


async def test_dedupe_is_per_code(monkeypatch) -> None:
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    redis = _FakeRedis()
    monkeypatch.setattr(sn, "_get_redis", lambda: redis)

    posted: list = []

    async def _post(api_key, payload):
        posted.append(payload)

    monkeypatch.setattr(sn, "_post_to_resend", _post)

    await sn._notify_async(ec.get_spec(ec.QF_COST_CEILING), trace_id="a", path="/x", context=None)
    await sn._notify_async(ec.get_spec(ec.QF_LLM_PROVIDER_DOWN), trace_id="b", path="/x", context=None)
    # Different codes → both send.
    assert len(posted) == 2


async def test_dedupe_uses_configured_ttl(monkeypatch) -> None:
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    captured = {}

    class _CapRedis:
        async def set(self, key, value, nx=False, ex=None):
            captured["key"] = key
            captured["nx"] = nx
            captured["ex"] = ex
            return True

    monkeypatch.setattr(sn, "_get_redis", lambda: _CapRedis())

    async def _post(api_key, payload):
        return None

    monkeypatch.setattr(sn, "_post_to_resend", _post)
    await sn._notify_async(ec.get_spec(ec.QF_UNKNOWN), trace_id="t", path="/x", context=None)
    assert captured["nx"] is True
    assert captured["ex"] == sn.DEDUPE_TTL_S
    assert "QF-UNKNOWN" in captured["key"]


# ---------------------------------------------------------------------------
# Fail-open
# ---------------------------------------------------------------------------

async def test_redis_fault_skips_send(monkeypatch) -> None:
    """A Redis fault must NOT send (avoid an email storm during an outage)."""
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(sn, "_get_redis", lambda: _BoomRedis())

    posted: list = []

    async def _post(api_key, payload):
        posted.append(payload)

    monkeypatch.setattr(sn, "_post_to_resend", _post)
    # Must not raise, must not send.
    await sn._notify_async(ec.get_spec(ec.QF_UNKNOWN), trace_id="t", path="/x", context=None)
    assert posted == []


async def test_no_redis_skips_send(monkeypatch) -> None:
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(sn, "_get_redis", lambda: None)

    posted: list = []

    async def _post(api_key, payload):
        posted.append(payload)

    monkeypatch.setattr(sn, "_post_to_resend", _post)
    await sn._notify_async(ec.get_spec(ec.QF_UNKNOWN), trace_id="t", path="/x", context=None)
    assert posted == []


async def test_post_failure_never_raises(monkeypatch) -> None:
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(sn, "_get_redis", lambda: _FakeRedis())

    async def _boom(api_key, payload):
        raise RuntimeError("network down")

    monkeypatch.setattr(sn, "_post_to_resend", _boom)
    # Must swallow the POST error.
    await sn._notify_async(ec.get_spec(ec.QF_UNKNOWN), trace_id="t", path="/x", context=None)


# ---------------------------------------------------------------------------
# Payload shape (non-PII)
# ---------------------------------------------------------------------------

def test_payload_contains_code_trace_and_no_pii() -> None:
    spec = ec.get_spec(ec.QF_AGENT_FAILED)
    payload = sn._build_payload(
        spec,
        trace_id="trace-xyz",
        path="/quiz",
        context={"quiz_id": "abc", "error_type": "TimeoutError", "secret_pii": object()},
    )
    assert spec.code in payload["subject"]
    text = payload["text"]
    assert spec.code in text
    assert "trace-xyz" in text
    assert "abc" in text  # quiz_id (a non-PII opaque id) ok
    assert "TimeoutError" in text
    # The non-scalar value is dropped (never serialised blindly).
    assert "secret_pii" not in text
    assert payload["to"] == ["support@quafel.com"]


# ---------------------------------------------------------------------------
# Fire-and-forget public entry
# ---------------------------------------------------------------------------

async def test_maybe_notify_support_is_fire_and_forget(monkeypatch) -> None:
    monkeypatch.setenv("RESEND_API_KEY", "re_test_key")
    monkeypatch.setattr(sn, "_get_redis", lambda: _FakeRedis())

    posted: list = []

    async def _post(api_key, payload):
        posted.append(payload)

    monkeypatch.setattr(sn, "_post_to_resend", _post)

    # Returns immediately (None) without awaiting the send.
    spec = ec.get_spec(ec.QF_UNKNOWN)
    ret = sn.maybe_notify_support(spec, trace_id="t", path="/x")
    assert ret is None
    # Let the scheduled task run.
    await asyncio.sleep(0.05)
    assert len(posted) == 1


def test_maybe_notify_support_is_noop_for_non_notify_code() -> None:
    # A non-notify code returns immediately without touching anything.
    spec = ec.get_spec(ec.QF_QUIZ_NOT_FOUND)
    assert spec.notify_support is False
    assert sn.maybe_notify_support(spec, trace_id="t") is None


def test_maybe_notify_support_no_event_loop_is_safe(monkeypatch) -> None:
    """Called from sync context (no running loop) → safe no-op, never raises."""
    spec = ec.get_spec(ec.QF_UNKNOWN)
    # No running loop here (plain sync test) → should not raise.
    assert sn.maybe_notify_support(spec, trace_id="t") is None
