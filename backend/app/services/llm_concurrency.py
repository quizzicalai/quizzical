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

Cluster-wide cap (P1, Scalability)
----------------------------------
The in-process semaphore bounds concurrency *per replica*. With K replicas the
real ceiling is ``capacity × K`` with no cross-process coordination, so it does
not bound provider spend / rate-limit at scale. When
``settings.llm.global_concurrency.enabled`` is True, ``acquire`` ALSO reserves a
slot from a Redis-backed counter under a single global key — layered on top of
the (always-active) local semaphore.

The cluster bound is strictly best-effort and MUST fail open: on ANY Redis
error, or when no Redis client is available, the limiter falls back to the
in-process semaphore only and logs a warning. A Redis blip must never block all
LLM calls cluster-wide.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Lua: atomic bounded concurrency counter (cluster-wide slot reservation)
# ---------------------------------------------------------------------------
# A simple gauge (not a token bucket): acquire increments iff below capacity,
# release decrements (floored at 0). A TTL is (re)applied on every mutation so
# a process that dies mid-call cannot leak a slot forever — the whole counter
# self-heals after ``ttl_seconds`` of total inactivity.
#
# KEYS[1] = counter key
# ARGV[1] = capacity      (int)
# ARGV[2] = ttl_seconds   (int)
# Returns: { acquired (1/0), current (int) }
GLOBAL_CONCURRENCY_ACQUIRE_LUA = """
local capacity = tonumber(ARGV[1])
local ttl      = tonumber(ARGV[2])
local current  = tonumber(redis.call('GET', KEYS[1]) or '0')
if current < capacity then
  current = redis.call('INCR', KEYS[1])
  redis.call('EXPIRE', KEYS[1], ttl)
  return { 1, current }
else
  -- Refresh TTL so a saturated-but-live key never expires out from under us.
  redis.call('EXPIRE', KEYS[1], ttl)
  return { 0, current }
end
"""

# KEYS[1] = counter key
# ARGV[1] = ttl_seconds   (int)
# Returns: current (int)
GLOBAL_CONCURRENCY_RELEASE_LUA = """
local ttl     = tonumber(ARGV[1])
local current = tonumber(redis.call('GET', KEYS[1]) or '0')
if current <= 1 then
  redis.call('DEL', KEYS[1])
  return 0
end
current = redis.call('DECR', KEYS[1])
if current < 0 then
  redis.call('SET', KEYS[1], 0)
  current = 0
end
redis.call('EXPIRE', KEYS[1], ttl)
return current
"""


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


class _ClusterConcurrencyGate:
    """Best-effort Redis-backed cluster-wide concurrency gate.

    Acquire/release operate on a single global counter key. Every public method
    fails open: on ANY error a warning is logged and the caller proceeds with
    the in-process semaphore only.
    """

    # TTL (seconds) applied to the counter key. Generously larger than any
    # single LLM call so a live, saturated cluster never expires the key, while
    # still self-healing leaked slots after a crash within a bounded window.
    _ttl_seconds = 900

    def __init__(
        self,
        *,
        redis_factory: Any,
        capacity: int,
        namespace: str,
        acquire_timeout_s: float,
        poll_interval_s: float,
    ) -> None:
        self._redis_factory = redis_factory
        self._capacity = int(capacity)
        self._key = f"{namespace}:slots"
        self._acquire_timeout_s = float(acquire_timeout_s)
        self._poll_interval_s = float(poll_interval_s)

    def _get_redis(self) -> Any | None:
        """Resolve a redis client lazily. Returns None on any failure."""
        factory = self._redis_factory
        if factory is None:
            return None
        try:
            client = factory()
        except Exception:
            # e.g. pool not initialised yet (HTTPException) — behave as today.
            return None
        return client or None

    async def acquire(self, *, tool: str) -> bool:
        """Reserve a cluster slot. Returns True iff a slot was reserved in Redis.

        Returns False (fail-open) when disabled by absence of redis, on any
        Redis error, or — if a finite timeout is set — when the cluster cap is
        saturated for the whole wait window. A False return means the caller
        proceeds under the local semaphore only; it never blocks LLM calls.
        """
        redis = self._get_redis()
        if redis is None:
            return False

        start = time.perf_counter()
        deadline = start + self._acquire_timeout_s if self._acquire_timeout_s > 0 else None
        logged_wait = False
        while True:
            try:
                res = await redis.eval(
                    GLOBAL_CONCURRENCY_ACQUIRE_LUA,
                    1,
                    self._key,
                    str(self._capacity),
                    str(self._ttl_seconds),
                )
                acquired = bool(int(res[0]))
                current = int(res[1])
            except Exception as e:
                # Fail open — never block LLM calls on a Redis fault.
                logger.warning(
                    "llm.concurrency.cluster.fail_open",
                    tool=tool,
                    where="acquire",
                    error=str(e),
                )
                return False

            if acquired:
                return True

            # Saturated cluster-wide. With no wait budget, fall back immediately.
            if deadline is None:
                logger.info(
                    "llm.concurrency.cluster.saturated_fallback",
                    tool=tool,
                    capacity=self._capacity,
                    current=current,
                )
                return False

            if not logged_wait:
                logged_wait = True
                logger.info(
                    "llm.concurrency.cluster.wait",
                    tool=tool,
                    capacity=self._capacity,
                    current=current,
                )

            if time.perf_counter() >= deadline:
                # Waited the full window without a slot — fall back to local-only
                # rather than failing the request, keeping the hot path resilient.
                logger.warning(
                    "llm.concurrency.cluster.wait_timeout_fallback",
                    tool=tool,
                    capacity=self._capacity,
                    current=current,
                    waited_s=round(time.perf_counter() - start, 3),
                )
                return False

            remaining = deadline - time.perf_counter()
            await asyncio.sleep(min(self._poll_interval_s, max(0.0, remaining)))

    async def release(self, *, tool: str) -> None:
        """Release a previously reserved cluster slot. Fails open silently."""
        redis = self._get_redis()
        if redis is None:
            return
        try:
            await redis.eval(
                GLOBAL_CONCURRENCY_RELEASE_LUA,
                1,
                self._key,
                str(self._ttl_seconds),
            )
        except Exception as e:
            logger.warning(
                "llm.concurrency.cluster.fail_open",
                tool=tool,
                where="release",
                error=str(e),
            )


class LLMConcurrencyLimiter:
    """Bounded concurrency limiter for LLM calls.

    AC-SCALE-LLM-1..6. Optionally layered with a cluster-wide (Redis) cap.
    """

    def __init__(
        self,
        *,
        capacity: int,
        acquire_timeout_s: float,
        cluster_gate: _ClusterConcurrencyGate | None = None,
    ) -> None:
        if capacity is None or int(capacity) < 1:
            raise ValueError("LLM concurrency capacity must be >= 1")
        if acquire_timeout_s is None or float(acquire_timeout_s) < 0:
            raise ValueError("LLM concurrency acquire_timeout_s must be >= 0")
        self._capacity = int(capacity)
        self._acquire_timeout_s = float(acquire_timeout_s)
        self._sem = asyncio.Semaphore(self._capacity)
        self._cluster_gate = cluster_gate
        self._in_flight = 0
        self._total_acquired = 0
        self._total_timeouts = 0
        self._total_cluster_acquired = 0
        self._total_cluster_fallbacks = 0

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def cluster_enabled(self) -> bool:
        return self._cluster_gate is not None

    def metrics(self) -> dict[str, Any]:
        """AC-SCALE-LLM-6 — observability snapshot. Never raises."""
        return {
            "capacity": self._capacity,
            "in_flight": self._in_flight,
            "available": max(0, self._capacity - self._in_flight),
            "total_acquired": self._total_acquired,
            "total_timeouts": self._total_timeouts,
            "cluster_enabled": self._cluster_gate is not None,
            "total_cluster_acquired": self._total_cluster_acquired,
            "total_cluster_fallbacks": self._total_cluster_fallbacks,
        }

    @contextlib.asynccontextmanager
    async def acquire(self, *, tool: str = "unknown") -> AsyncIterator[None]:
        """Acquire a slot, waiting up to ``acquire_timeout_s`` seconds.

        Raises ``LLMConcurrencyTimeoutError`` on timeout of the *local*
        semaphore. The slot is always released on exit, even when the wrapped
        block raises.

        When a cluster gate is configured, a Redis-backed slot is ALSO acquired
        (after the local semaphore) and released on exit. The cluster gate is
        strictly best-effort: any Redis failure falls back to the local bound.
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

        # Local slot held. Now (optionally) reserve a cluster-wide slot. This is
        # best-effort and never raises — on any fault we proceed local-only.
        cluster_held = False
        if self._cluster_gate is not None:
            try:
                cluster_held = await self._cluster_gate.acquire(tool=tool)
            except Exception:  # pragma: no cover — gate.acquire already fails open.
                logger.warning(
                    "llm.concurrency.cluster.fail_open",
                    tool=tool,
                    where="acquire_outer",
                    exc_info=True,
                )
                cluster_held = False
            if cluster_held:
                self._total_cluster_acquired += 1
            else:
                self._total_cluster_fallbacks += 1

        self._in_flight += 1
        self._total_acquired += 1
        waited_s = time.perf_counter() - start
        logger.debug(
            "llm.concurrency.acquired",
            tool=tool,
            in_flight=self._in_flight,
            capacity=self._capacity,
            waited_s=round(waited_s, 3),
            cluster_held=cluster_held,
        )

        try:
            yield
        finally:
            self._in_flight -= 1
            if cluster_held and self._cluster_gate is not None:
                try:
                    await self._cluster_gate.release(tool=tool)
                except Exception:  # pragma: no cover — gate.release already fails open.
                    logger.warning(
                        "llm.concurrency.cluster.fail_open",
                        tool=tool,
                        where="release_outer",
                        exc_info=True,
                    )
            try:
                self._sem.release()
            except Exception:  # pragma: no cover — Semaphore.release never raises in practice.
                logger.warning("llm.concurrency.release_failed", tool=tool, exc_info=True)


# ---------------------------------------------------------------------------
# Process-global accessor
# ---------------------------------------------------------------------------

_global_limiter: LLMConcurrencyLimiter | None = None


def _default_redis_factory() -> Any | None:
    """Resolve the app's shared redis client, lazily and defensively.

    Mirrors how other best-effort cluster features obtain redis (the shared
    pool via ``app.api.dependencies.get_redis_client``). Returns None on any
    failure (e.g. pool not yet initialised) so the limiter behaves exactly as
    today when no redis is available.
    """
    try:
        from app.api.dependencies import get_redis_client

        return get_redis_client()
    except Exception:
        return None


def _build_cluster_gate(llm_cfg: Any) -> _ClusterConcurrencyGate | None:
    """Construct a cluster gate from settings, or None when disabled/misconfig."""
    gc_cfg = getattr(llm_cfg, "global_concurrency", None)
    if gc_cfg is None or not bool(getattr(gc_cfg, "enabled", False)):
        return None
    try:
        capacity = int(getattr(gc_cfg, "max_concurrent", 64) or 64)
        namespace = str(getattr(gc_cfg, "namespace", "quizzical:llm:concurrency"))
        acquire_timeout_s = float(getattr(gc_cfg, "acquire_timeout_s", 10.0) or 0.0)
        poll_interval_s = float(getattr(gc_cfg, "poll_interval_s", 0.05) or 0.05)
    except Exception:
        logger.warning("llm.concurrency.cluster.config_invalid", exc_info=True)
        return None
    return _ClusterConcurrencyGate(
        redis_factory=_default_redis_factory,
        capacity=capacity,
        namespace=namespace,
        acquire_timeout_s=acquire_timeout_s,
        poll_interval_s=poll_interval_s,
    )


def _build_limiter_from_settings() -> LLMConcurrencyLimiter:
    """Construct a limiter using current settings (with safe defaults)."""
    cluster_gate: _ClusterConcurrencyGate | None = None
    try:
        from app.core.config import settings

        llm_cfg = getattr(settings, "llm", None)
        capacity = int(getattr(llm_cfg, "max_concurrency", 16) or 16)
        timeout_s = float(getattr(llm_cfg, "acquire_timeout_s", 30.0) or 30.0)
        cluster_gate = _build_cluster_gate(llm_cfg)
    except Exception:
        capacity = 16
        timeout_s = 30.0
        cluster_gate = None
    if cluster_gate is not None:
        logger.info(
            "llm.concurrency.cluster.enabled",
            capacity=cluster_gate._capacity,
            key=cluster_gate._key,
        )
    return LLMConcurrencyLimiter(
        capacity=capacity, acquire_timeout_s=timeout_s, cluster_gate=cluster_gate
    )


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
