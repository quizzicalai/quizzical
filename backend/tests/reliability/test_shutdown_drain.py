"""§17.2 — Graceful Shutdown Drain (AC-SCALE-SHUTDOWN-*).

Tests target the ``app.main._drain_in_flight_work`` helper that the lifespan
calls before disposing of resources.
"""

from __future__ import annotations

import asyncio
import logging
import time

import pytest


@pytest.mark.asyncio
async def test_drain_returns_immediately_when_no_in_flight_work() -> None:
    """AC-SCALE-SHUTDOWN-1: with in_flight=0 the drain returns near-instantly."""
    from app.main import _drain_in_flight_work
    from app.services.llm_concurrency import LLMConcurrencyLimiter

    limiter = LLMConcurrencyLimiter(capacity=4, acquire_timeout_s=1.0)
    start = time.perf_counter()
    elapsed_s, residual = await _drain_in_flight_work(limiter, grace_s=2.0)
    elapsed_real = time.perf_counter() - start
    assert residual == 0
    assert elapsed_s < 0.1
    assert elapsed_real < 0.5


@pytest.mark.asyncio
async def test_drain_waits_for_in_flight_to_finish() -> None:
    """AC-SCALE-SHUTDOWN-1: drain waits until in_flight reaches 0."""
    from app.main import _drain_in_flight_work
    from app.services.llm_concurrency import LLMConcurrencyLimiter

    limiter = LLMConcurrencyLimiter(capacity=2, acquire_timeout_s=1.0)
    release = asyncio.Event()

    async def _holder() -> None:
        async with limiter.acquire(tool="t"):
            await release.wait()

    holder = asyncio.create_task(_holder())
    await asyncio.sleep(0.02)
    assert limiter.metrics()["in_flight"] == 1

    drain_task = asyncio.create_task(_drain_in_flight_work(limiter, grace_s=2.0))
    # Drain task must NOT complete while the holder still has the slot.
    await asyncio.sleep(0.1)
    assert not drain_task.done()

    release.set()
    elapsed_s, residual = await asyncio.wait_for(drain_task, timeout=2.0)
    await holder
    assert residual == 0
    assert elapsed_s >= 0.1


@pytest.mark.asyncio
async def test_drain_returns_with_residual_when_grace_expires(caplog: pytest.LogCaptureFixture) -> None:
    """AC-SCALE-SHUTDOWN-2: drain logs a warning and returns the residual count."""
    from app.main import _drain_in_flight_work
    from app.services.llm_concurrency import LLMConcurrencyLimiter

    limiter = LLMConcurrencyLimiter(capacity=2, acquire_timeout_s=1.0)
    keep_busy = asyncio.Event()

    async def _holder() -> None:
        async with limiter.acquire(tool="busy"):
            await keep_busy.wait()

    holders = [asyncio.create_task(_holder()) for _ in range(2)]
    await asyncio.sleep(0.02)
    assert limiter.metrics()["in_flight"] == 2

    elapsed_s, residual = await _drain_in_flight_work(limiter, grace_s=0.2)
    assert residual == 2
    assert 0.2 <= elapsed_s < 1.0

    keep_busy.set()
    await asyncio.gather(*holders)


@pytest.mark.asyncio
async def test_drain_disabled_when_grace_zero() -> None:
    """AC-SCALE-SHUTDOWN-4: grace_s=0 → no wait at all, even if in_flight>0."""
    from app.main import _drain_in_flight_work
    from app.services.llm_concurrency import LLMConcurrencyLimiter

    limiter = LLMConcurrencyLimiter(capacity=1, acquire_timeout_s=1.0)
    keep_busy = asyncio.Event()

    async def _holder() -> None:
        async with limiter.acquire(tool="busy"):
            await keep_busy.wait()

    holder = asyncio.create_task(_holder())
    await asyncio.sleep(0.02)

    start = time.perf_counter()
    elapsed_s, residual = await _drain_in_flight_work(limiter, grace_s=0.0)
    elapsed_real = time.perf_counter() - start
    assert elapsed_real < 0.05
    assert residual == 1
    assert elapsed_s == 0.0

    keep_busy.set()
    await holder


def test_settings_shutdown_grace_validation() -> None:
    """AC-SCALE-SHUTDOWN-4: negative shutdown_grace_s rejected at config time."""
    from app.core.config import Settings

    with pytest.raises(Exception):
        Settings(shutdown_grace_s=-1.0)
    s = Settings(shutdown_grace_s=0)
    assert s.shutdown_grace_s == 0
