"""§17.1 — Global LLM Concurrency Semaphore (AC-SCALE-LLM-*).

A thin, observable wrapper around ``asyncio.Semaphore`` that bounds the number
of concurrent LLM calls process-wide. Acquiring is timeout-aware so requests
fail fast under saturation rather than blocking the event loop indefinitely.

Design notes
------------
- The limiter is *lazy*: a process-global instance is constructed on first
  ``get_global_limiter()`` call using values from ``settings.llm``. This keeps
  imports cheap and avoids ordering issues with the FastAPI lifespan.
- ``acquire()`` is an async context manager that records counters and emits
  structured logs. It releases on exception so the counter can never leak.
- ``metrics()`` returns a snapshot dict — no locks, intentionally racy, used
  for tests/observability not for control flow.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class LLMConcurrencyTimeoutError(RuntimeError):
    """Raised when ``LLMConcurrencyLimiter.acquire`` times out waiting for a slot."""

    def __init__(self, *, tool: str, capacity: int, waited_s: float) -> None:
        super().__init__(
            f"LLM concurrency limit reached (capacity={capacity}); "
            f"tool={tool!r} waited {waited_s:.3f}s"
        )
        self.tool = tool
        self.capacity = capacity
        self.waited_s = waited_s


class LLMConcurrencyLimiter:
    """Bounded concurrency limiter for LLM calls.

    AC-SCALE-LLM-1..6.
    """

    def __init__(self, *, capacity: int, acquire_timeout_s: float) -> None:
        if capacity is None or int(capacity) < 1:
            raise ValueError("LLM concurrency capacity must be >= 1")
        if acquire_timeout_s is None or float(acquire_timeout_s) < 0:
            raise ValueError("LLM concurrency acquire_timeout_s must be >= 0")
        self._capacity = int(capacity)
        self._acquire_timeout_s = float(acquire_timeout_s)
        self._sem = asyncio.Semaphore(self._capacity)
        self._in_flight = 0
        self._total_acquired = 0
        self._total_timeouts = 0

    @property
    def capacity(self) -> int:
        return self._capacity

    def metrics(self) -> dict[str, Any]:
        """AC-SCALE-LLM-6 — observability snapshot. Never raises."""
        return {
            "capacity": self._capacity,
            "in_flight": self._in_flight,
            "available": max(0, self._capacity - self._in_flight),
            "total_acquired": self._total_acquired,
            "total_timeouts": self._total_timeouts,
        }

    @contextlib.asynccontextmanager
    async def acquire(self, *, tool: str = "unknown") -> AsyncIterator[None]:
        """Acquire a slot, waiting up to ``acquire_timeout_s`` seconds.

        Raises ``LLMConcurrencyTimeoutError`` on timeout. The slot is always
        released on exit, even when the wrapped block raises.
        """
        start = time.perf_counter()
        # Fast path: no waiters → log at debug; else log at info with wait estimate.
        currently_used = self._in_flight
        if currently_used >= self._capacity:
            logger.info(
                "llm.concurrency.wait",
                tool=tool,
                in_flight=currently_used,
                capacity=self._capacity,
            )

        try:
            if self._acquire_timeout_s > 0:
                await asyncio.wait_for(
                    self._sem.acquire(), timeout=self._acquire_timeout_s
                )
            else:
                await self._sem.acquire()
        except asyncio.TimeoutError as exc:
            self._total_timeouts += 1
            waited_s = time.perf_counter() - start
            logger.warning(
                "llm.concurrency.timeout",
                tool=tool,
                capacity=self._capacity,
                in_flight=self._in_flight,
                waited_s=round(waited_s, 3),
            )
            raise LLMConcurrencyTimeoutError(
                tool=tool, capacity=self._capacity, waited_s=waited_s
            ) from exc

        self._in_flight += 1
        self._total_acquired += 1
        waited_s = time.perf_counter() - start
        logger.debug(
            "llm.concurrency.acquired",
            tool=tool,
            in_flight=self._in_flight,
            capacity=self._capacity,
            waited_s=round(waited_s, 3),
        )

        try:
            yield
        finally:
            self._in_flight -= 1
            try:
                self._sem.release()
            except Exception:  # pragma: no cover — Semaphore.release never raises in practice.
                logger.warning("llm.concurrency.release_failed", tool=tool, exc_info=True)


# ---------------------------------------------------------------------------
# Process-global accessor
# ---------------------------------------------------------------------------

_global_limiter: LLMConcurrencyLimiter | None = None


def _build_limiter_from_settings() -> LLMConcurrencyLimiter:
    """Construct a limiter using current settings (with safe defaults)."""
    try:
        from app.core.config import settings

        llm_cfg = getattr(settings, "llm", None)
        capacity = int(getattr(llm_cfg, "max_concurrency", 16) or 16)
        timeout_s = float(getattr(llm_cfg, "acquire_timeout_s", 30.0) or 30.0)
    except Exception:
        capacity = 16
        timeout_s = 30.0
    return LLMConcurrencyLimiter(capacity=capacity, acquire_timeout_s=timeout_s)


def get_global_limiter() -> LLMConcurrencyLimiter:
    """Return the process-global limiter, constructing it on first use."""
    global _global_limiter
    if _global_limiter is None:
        _global_limiter = _build_limiter_from_settings()
    return _global_limiter


def reset_global_limiter_for_tests() -> None:
    """Test helper — drop the cached limiter so settings changes take effect."""
    global _global_limiter
    _global_limiter = None


__all__ = [
    "LLMConcurrencyLimiter",
    "LLMConcurrencyTimeoutError",
    "get_global_limiter",
    "reset_global_limiter_for_tests",
]
