# tests/unit/services/test_llm_concurrency.py
"""Tests for §17.1 — Global LLM Concurrency Semaphore (AC-SCALE-LLM-*).

These tests target ``app.services.llm_concurrency`` (a thin wrapper around
``asyncio.Semaphore``) and its integration into ``LLMService.get_structured_response``.
The wrapper is created lazily on first acquire so tests can poke it without a
running app lifespan.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest


@pytest.mark.asyncio
async def test_acquire_below_capacity_is_immediate() -> None:
    """AC-SCALE-LLM-1: under-capacity acquire returns immediately."""
    from app.services.llm_concurrency import LLMConcurrencyLimiter

    limiter = LLMConcurrencyLimiter(capacity=4, acquire_timeout_s=1.0)

    async with limiter.acquire(tool="test"):
        m = limiter.metrics()
        assert m["capacity"] == 4
        assert m["in_flight"] == 1
        assert m["available"] == 3

    m = limiter.metrics()
    assert m["in_flight"] == 0
    assert m["available"] == 4
    assert m["total_acquired"] == 1
    assert m["total_timeouts"] == 0


@pytest.mark.asyncio
async def test_acquire_blocks_when_full_until_release() -> None:
    """AC-SCALE-LLM-2: when capacity is exhausted, callers wait until a slot frees."""
    from app.services.llm_concurrency import LLMConcurrencyLimiter

    limiter = LLMConcurrencyLimiter(capacity=1, acquire_timeout_s=2.0)

    holder_release = asyncio.Event()
    waiter_acquired = asyncio.Event()

    async def _holder() -> None:
        async with limiter.acquire(tool="holder"):
            await holder_release.wait()

    async def _waiter() -> None:
        async with limiter.acquire(tool="waiter"):
            waiter_acquired.set()

    h = asyncio.create_task(_holder())
    # Give holder a chance to acquire.
    await asyncio.sleep(0.01)
    assert limiter.metrics()["in_flight"] == 1

    w = asyncio.create_task(_waiter())
    await asyncio.sleep(0.05)
    # Waiter should still be blocked.
    assert not waiter_acquired.is_set()
    assert limiter.metrics()["in_flight"] == 1

    holder_release.set()
    await asyncio.wait_for(asyncio.gather(h, w), timeout=2.0)
    assert waiter_acquired.is_set()
    assert limiter.metrics()["in_flight"] == 0
    assert limiter.metrics()["total_acquired"] == 2


@pytest.mark.asyncio
async def test_acquire_timeout_raises_concurrency_timeout() -> None:
    """AC-SCALE-LLM-2: timeout raises LLMConcurrencyTimeoutError, total_timeouts++."""
    from app.services.llm_concurrency import (
        LLMConcurrencyLimiter,
        LLMConcurrencyTimeoutError,
    )

    limiter = LLMConcurrencyLimiter(capacity=1, acquire_timeout_s=0.05)

    async def _hold_forever() -> None:
        async with limiter.acquire(tool="holder"):
            await asyncio.sleep(1.0)

    holder = asyncio.create_task(_hold_forever())
    await asyncio.sleep(0.01)

    with pytest.raises(LLMConcurrencyTimeoutError):
        async with limiter.acquire(tool="late"):
            pass  # pragma: no cover

    assert limiter.metrics()["total_timeouts"] >= 1
    holder.cancel()
    try:
        await holder
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_release_on_inner_exception() -> None:
    """AC-SCALE-LLM-3: inner exception still releases the semaphore."""
    from app.services.llm_concurrency import LLMConcurrencyLimiter

    limiter = LLMConcurrencyLimiter(capacity=2, acquire_timeout_s=1.0)

    class _Boom(RuntimeError):
        pass

    with pytest.raises(_Boom):
        async with limiter.acquire(tool="t"):
            raise _Boom("inner failure")

    m = limiter.metrics()
    assert m["in_flight"] == 0
    assert m["available"] == 2


def test_invalid_capacity_rejected() -> None:
    """AC-SCALE-LLM-5: capacity <= 0 raises at construction time."""
    from app.services.llm_concurrency import LLMConcurrencyLimiter

    with pytest.raises(ValueError):
        LLMConcurrencyLimiter(capacity=0, acquire_timeout_s=1.0)
    with pytest.raises(ValueError):
        LLMConcurrencyLimiter(capacity=-1, acquire_timeout_s=1.0)


@pytest.mark.asyncio
async def test_metrics_never_raise_and_are_consistent() -> None:
    """AC-SCALE-LLM-6: metrics() always returns a complete dict."""
    from app.services.llm_concurrency import LLMConcurrencyLimiter

    limiter = LLMConcurrencyLimiter(capacity=3, acquire_timeout_s=1.0)
    m = limiter.metrics()
    assert {"capacity", "in_flight", "available", "total_acquired", "total_timeouts"} <= set(m.keys())
    assert m["capacity"] == 3
    assert m["in_flight"] == 0
    assert m["available"] == 3


@pytest.mark.asyncio
async def test_llm_service_uses_global_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: LLMService.get_structured_response acquires the global limiter."""
    from app.services import llm_concurrency, llm_service as llm_mod

    # Reset & install a tight limiter so we can observe metrics.
    test_limiter = llm_concurrency.LLMConcurrencyLimiter(capacity=2, acquire_timeout_s=1.0)
    monkeypatch.setattr(llm_concurrency, "_global_limiter", test_limiter, raising=False)
    monkeypatch.setattr(
        llm_concurrency, "get_global_limiter", lambda: test_limiter, raising=False
    )

    captured_in_flight: list[int] = []

    async def _fake_call(**kwargs: Any) -> Any:
        # Inside the LLM call, the limiter must show an in-flight slot.
        captured_in_flight.append(test_limiter.metrics()["in_flight"])
        # Return a minimal structured payload.
        return {
            "id": "resp_1",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": '{"ok": true}'}],
                }
            ],
        }

    # Patch litellm.responses (called via asyncio.to_thread).
    import litellm  # noqa: WPS433

    def _sync_call(**kwargs: Any) -> Any:
        # Simulate a measurable sync call inside to_thread.
        return asyncio.run(_fake_call(**kwargs)) if False else {
            "id": "resp_1",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": '{"ok": true}'}],
                }
            ],
        }

    monkeypatch.setattr(litellm, "responses", _sync_call, raising=True)

    from pydantic import BaseModel

    class _Schema(BaseModel):
        ok: bool

    svc = llm_mod.LLMService()
    result = await svc.get_structured_response(
        tool_name="unit_test",
        messages=[{"role": "user", "content": "hi"}],
        response_model=_Schema,
    )
    assert isinstance(result, _Schema) and result.ok is True

    final = test_limiter.metrics()
    assert final["in_flight"] == 0
    assert final["total_acquired"] >= 1
