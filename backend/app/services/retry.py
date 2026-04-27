# app/services/retry.py
"""Reliability §16 — bounded retry with exponential backoff + jitter.

Shared between LLM (`llm_service.py`) and image (`image_service.py`) call
sites. Async-only. Never sleeps when ``max_attempts <= 1`` so callers can
disable retry purely via config.

The helper does NOT classify exceptions itself — callers pass an
``is_transient(exc) -> bool`` predicate so each call site can scope its
retriable error set narrowly. This keeps the helper free of LLM/FAL
dependencies and prevents accidentally retrying programmer bugs.
"""
from __future__ import annotations

import asyncio
import random
from typing import Awaitable, Callable, Optional, TypeVar

T = TypeVar("T")


async def retry_async(
    func: Callable[[], Awaitable[T]],
    *,
    is_transient: Callable[[BaseException], bool],
    max_attempts: int = 3,
    base_ms: int = 200,
    cap_ms: int = 2000,
    on_retry: Optional[Callable[[int, BaseException, float], None]] = None,
) -> T:
    """Run ``func()`` up to ``max_attempts`` times with exponential backoff.

    Backoff: ``min(cap_ms, base_ms * 2 ** attempt) + uniform[0, base_ms)`` ms.
    Re-raises the last exception when retries are exhausted or when
    ``is_transient`` returns False for an exception.
    """
    if max_attempts < 1:
        max_attempts = 1
    attempt = 0
    while True:
        try:
            return await func()
        except BaseException as e:  # noqa: BLE001 — caller decides what's transient
            attempt += 1
            if attempt >= max_attempts or not is_transient(e):
                raise
            backoff_ms = min(cap_ms, base_ms * (2 ** (attempt - 1)))
            jitter_ms = random.uniform(0, base_ms)
            delay_s = (backoff_ms + jitter_ms) / 1000.0
            if on_retry is not None:
                try:
                    on_retry(attempt, e, delay_s)
                except Exception:  # pragma: no cover — logging must not break retry
                    pass
            await asyncio.sleep(delay_s)
