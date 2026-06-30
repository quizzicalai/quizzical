"""Hitlist #15 — cold-start LLM pre-warm.

``warm_up`` must:
  * trigger LiteLLM's one-time lazy init (cost-map / tokenizer) WITHOUT any
    network/completion call (so it cannot spend money or need a key);
  * run the CPU-bound work off the event loop;
  * be idempotent (warm at most once per process);
  * be fail-open (a warm-up fault never raises out of ``warm_up``).
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.unit]


@pytest.fixture(autouse=True)
def _reset_warm_flag(monkeypatch):
    """Reset the process-wide warmed flag so each test starts cold."""
    import app.services.llm_service as svc
    monkeypatch.setattr(svc, "_warmed_up", False, raising=False)
    yield


@pytest.mark.asyncio
async def test_warm_up_invokes_sync_once_and_is_idempotent(monkeypatch):
    import app.services.llm_service as svc

    calls = {"n": 0}

    def _fake_sync():
        calls["n"] += 1

    monkeypatch.setattr(svc, "_warm_up_sync", _fake_sync, raising=False)

    await svc.warm_up()
    await svc.warm_up()  # second call must be a no-op

    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_warm_up_is_fail_open(monkeypatch):
    import app.services.llm_service as svc

    def _boom():
        raise RuntimeError("warmup blew up")

    monkeypatch.setattr(svc, "_warm_up_sync", _boom, raising=False)

    # MUST NOT raise.
    await svc.warm_up()


@pytest.mark.asyncio
async def test_warm_up_makes_no_network_call(monkeypatch):
    """The warm-up must NOT call litellm.responses/completion (no provider hit,
    no spend, no key requirement)."""
    import litellm

    import app.services.llm_service as svc

    def _explode(*a, **kw):
        raise AssertionError("warm-up must not make a model/network call")

    monkeypatch.setattr(litellm, "responses", _explode, raising=False)
    monkeypatch.setattr(litellm, "completion", _explode, raising=False)
    monkeypatch.setattr(litellm, "acompletion", _explode, raising=False)

    # Runs the real _warm_up_sync (local cost-map/tokenizer work only).
    await svc.warm_up()


def test_warm_up_sync_swallows_internal_faults(monkeypatch):
    """Each step inside _warm_up_sync is independently guarded; a fault in one
    (e.g. token_counter) must not abort the others or raise."""
    import litellm

    import app.services.llm_service as svc

    def _boom(*a, **kw):
        raise RuntimeError("nope")

    monkeypatch.setattr(litellm, "token_counter", _boom, raising=False)
    monkeypatch.setattr(litellm, "get_model_info", _boom, raising=False)

    # MUST NOT raise.
    svc._warm_up_sync()
