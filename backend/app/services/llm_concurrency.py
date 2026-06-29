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
# release decrements (floored at 0). The TTL is (re)applied ONLY when the count
# actually mutates (INCR on grant, DECR/DEL on release) — never on a
# non-granting saturated probe. This is deliberate: refreshing the TTL on every
# saturated probe under sustained traffic would perpetually keep the key alive,
# so a slot leaked by a crashed / failed-release holder (rolling deploy, OOM,
# Redis blip) would NEVER be reclaimed and ``current`` would drift monotonically
# up until every replica saw the cluster as saturated. By bounding the TTL to
# the most recent mutation, a leaked slot self-heals after ``ttl_seconds`` of
# mutation-inactivity (i.e. no successful acquire/release touches the key).
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
  -- Saturated: do NOT refresh the TTL. Refreshing on every non-granting probe
  -- would pin a leaked slot alive forever; bounding the TTL to real mutations
  -- lets the counter self-heal after ``ttl`` seconds of acquire/release silence.
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

    # TTL (seconds) applied to the counter key, refreshed only on a real mutation
    # (grant / release). Generously larger than any single LLM call so a live
    # cluster that keeps acquiring/releasing never expires the key, while a slot
    # leaked by a crashed holder self-heals after this much mutation-inactivity.
    _ttl_seconds = 900

    # Hard per-call ceiling (seconds) on a single ``redis.eval`` round-trip, so a
    # stalled Redis connection on the hot LLM path can never block longer than
    # this before we fail open to the local-only bound.
    _redis_op_timeout_s = 2.0

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

    async def _eval(self, script: str, *args: Any) -> Any:
        """Run a Lua script with a bounded timeout. Raises on timeout/error.

        A stuck Redis socket must not pin a local semaphore slot indefinitely,
        so each round-trip is wrapped in ``asyncio.wait_for``. Callers treat any
        raised exception as fail-open.
        """
        redis = self._get_redis()
        if redis is None:
            raise RuntimeError("no redis client available")
        return await asyncio.wait_for(
            redis.eval(script, 1, self._key, *args),
            timeout=self._redis_op_timeout_s,
        )

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
        if self._get_redis() is None:
            return False

        start = time.perf_counter()
        deadline = start + self._acquire_timeout_s if self._acquire_timeout_s > 0 else None
        logged_wait = False
        while True:
            try:
                res = await self._eval(
                    GLOBAL_CONCURRENCY_ACQUIRE_LUA,
                    str(self._capacity),
                    str(self._ttl_seconds),
                )
                acquired = bool(int(res[0]))
                current = int(res[1])
            except Exception as e:
                # Fail open — never block LLM calls on a Redis fault or a
                # round-trip that exceeded ``_redis_op_timeout_s``.
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
        if self._get_redis() is None:
            return
        try:
            await self._eval(
                GLOBAL_CONCURRENCY_RELEASE_LUA,
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

        Cancellation safety: once the local semaphore is held, all subsequent
        work runs inside a single try/finally whose finally releases the local
        semaphore unconditionally — including on ``asyncio.CancelledError`` —
        and does so BEFORE awaiting the (shielded) cluster release, so neither a
        cancelled cluster poll-wait nor a cancelled cluster release can leak the
        local slot.
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

        # The local semaphore is now held. EVERYTHING from here on lives inside a
        # single try/finally whose finally ALWAYS releases the local semaphore —
        # even on asyncio.CancelledError (a BaseException, not Exception), which
        # is routine on the hot path (graph wraps tool ainvoke in wait_for; the
        # ASGI server cancels on client disconnect). The cluster-slot acquire and
        # the body await both happen inside this try, so a cancellation during
        # the cluster poll-wait or the body can never leak the local slot.
        cluster_held = False
        # Track whether we incremented in_flight, so the finally only undoes work
        # that actually happened if cancelled mid-acquire.
        in_flight_incremented = False
        try:
            cluster_held = await self._reserve_cluster_slot(tool=tool)

            self._in_flight += 1
            in_flight_incremented = True
            self._total_acquired += 1
            logger.debug(
                "llm.concurrency.acquired",
                tool=tool,
                in_flight=self._in_flight,
                capacity=self._capacity,
                waited_s=round(time.perf_counter() - start, 3),
                cluster_held=cluster_held,
            )

            yield
        finally:
            if in_flight_incremented:
                self._in_flight -= 1
            await self._release_slots(tool=tool, cluster_held=cluster_held)

    async def _reserve_cluster_slot(self, *, tool: str) -> bool:
        """Best-effort cluster-slot acquire + counter bookkeeping.

        Returns True iff a Redis slot is held. Never raises for a Redis fault
        (the gate fails open); a ``CancelledError`` propagates so the caller's
        finally still releases the local slot.
        """
        if self._cluster_gate is None:
            return False
        try:
            cluster_held = await self._cluster_gate.acquire(tool=tool)
        except asyncio.CancelledError:
            raise
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
        return cluster_held

    async def _release_slots(self, *, tool: str, cluster_held: bool) -> None:
        """Release the LOCAL semaphore FIRST (synchronously), then the cluster.

        The local release must never be gated behind the awaited cluster
        release: if that await raised ``CancelledError`` the local slot would
        leak and the replica would permanently exhaust its limiter after
        ``capacity`` cancellations. ``asyncio.shield`` keeps a cancellation at
        the cluster-release point from aborting it mid-flight.
        """
        try:
            self._sem.release()
        except Exception:  # pragma: no cover — Semaphore.release never raises in practice.
            logger.warning("llm.concurrency.release_failed", tool=tool, exc_info=True)
        if cluster_held and self._cluster_gate is not None:
            try:
                await asyncio.shield(self._cluster_gate.release(tool=tool))
            except Exception:  # pragma: no cover — gate.release already fails open.
                logger.warning(
                    "llm.concurrency.cluster.fail_open",
                    tool=tool,
                    where="release_outer",
                    exc_info=True,
                )


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
        # Default 0.0 == single best-effort probe, no poll-wait (see config note).
        acquire_timeout_s = float(getattr(gc_cfg, "acquire_timeout_s", 0.0) or 0.0)
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
