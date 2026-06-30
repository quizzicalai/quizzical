"""Hitlist #3 (2026-06-30) — process-local fallback cost limiter.

Every cluster-wide cost guard fails OPEN on the shared Redis (a Redis blip must
not DoS legitimate users). That is the right default, but it means a *sustained*
Redis outage removes EVERY dollar ceiling — the live paid pipeline would run
uncapped for the duration of the outage. Turnstile (Redis-independent, fail-
closed) still gates bots so this is not directly exploitable, but there is no $
backstop.

This module adds a **coarse, in-memory, per-replica** start cap that engages
ONLY while Redis is unreachable. It is a DEGRADE, not a full fail-closed: each
replica independently allows a small fixed number of paid starts per rolling
window, so legitimate traffic keeps flowing (just throttled) during an outage,
and the cap evaporates the instant Redis recovers (the caller stops consulting
this limiter once the Redis read succeeds again).

Design:
  * Pure in-process state (a monotonic sliding window of recent start
    timestamps). No locks needed under CPython's single-threaded asyncio loop;
    the operations are synchronous and non-awaiting.
  * Window + cap come from ``settings.security.live_cost_guard`` so an operator
    can retune without a redeploy, with safe defaults.
  * ``allow_start()`` returns True/False; the caller raises the same 503 the
    Redis breaker raises when it returns False.
  * ``reset()`` clears state (used by tests).

This is intentionally conservative and replica-local: with N replicas the
cluster-wide allowance during an outage is ``N × cap_per_window`` — bounded and
known, vs unbounded today.
"""
from __future__ import annotations

import time
from collections import deque

import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)

# Module-local sliding window of recent admitted-start monotonic timestamps.
_starts: deque[float] = deque()

# Conservative defaults: 60 paid starts per 60s per replica while Redis is down.
# Generous enough that real users are not blocked during a brief blip, small
# enough to bound spend during a sustained outage.
_DEFAULT_CAP = 60
_DEFAULT_WINDOW_S = 60


def _cfg() -> tuple[int, int]:
    """(cap, window_seconds) from config, with safe defaults. Never raises."""
    cfg = getattr(getattr(settings, "security", None), "live_cost_guard", None)
    try:
        cap = int(getattr(cfg, "redis_outage_local_start_cap", _DEFAULT_CAP) or 0)
    except Exception:
        cap = _DEFAULT_CAP
    try:
        window = int(
            getattr(cfg, "redis_outage_local_window_s", _DEFAULT_WINDOW_S) or 0
        )
    except Exception:
        window = _DEFAULT_WINDOW_S
    if cap <= 0:
        cap = _DEFAULT_CAP
    if window <= 0:
        window = _DEFAULT_WINDOW_S
    return cap, window


def reset() -> None:
    """Clear the in-memory window (test hook)."""
    _starts.clear()


def allow_start(*, now: float | None = None) -> bool:
    """Coarse per-replica admission check for a PAID start while Redis is down.

    Returns True if this replica is still under its in-memory start cap for the
    current rolling window (and records the admission), False if the cap is hit.
    Synchronous + non-awaiting; safe to call inline on the request path.

    Best-effort: any internal error fails OPEN (returns True) — this limiter is a
    last-ditch backstop and must itself never become a source of user-facing
    failures.
    """
    try:
        cap, window = _cfg()
        t = float(now) if now is not None else time.monotonic()
        cutoff = t - window
        # Evict timestamps that fell out of the window.
        while _starts and _starts[0] < cutoff:
            _starts.popleft()
        if len(_starts) >= cap:
            logger.warning(
                "live_cost.local_fallback.cap_hit",
                cap=cap,
                window_s=window,
                in_window=len(_starts),
            )
            return False
        _starts.append(t)
        return True
    except Exception:
        logger.debug("live_cost.local_fallback.error", exc_info=True)
        return True
