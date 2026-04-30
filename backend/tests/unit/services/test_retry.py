"""Unit tests for ``app.services.retry`` (§16 bounded retry helper).

Covers behaviours not exercised by ``test_llm_retry`` /
``test_fal_retry`` (which test caller-specific transient predicates):

* Returns immediately on success.
* Retries on transient classification, re-raises after ``max_attempts``.
* Does NOT retry when ``is_transient`` returns ``False``.
* ``max_attempts <= 0`` is coerced to a single attempt (no sleep).
* ``on_retry`` callback receives ``(attempt, exc, delay)`` and exceptions
  raised by the callback are swallowed (logging must never break retry).
* Backoff delay is bounded by ``cap_ms + base_ms`` jitter ceiling and the
  helper does not sleep on the final failure.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.services.retry import retry_async

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Counter:
    def __init__(self) -> None:
        self.calls = 0

    async def succeed(self) -> str:
        self.calls += 1
        return "ok"

    def make_failer(
        self, *, fails: int, exc: BaseException
    ):
        async def _impl() -> str:
            self.calls += 1
            if self.calls <= fails:
                raise exc
            return "ok"

        return _impl

    def make_always_failing(self, exc: BaseException):
        async def _impl() -> str:
            self.calls += 1
            raise exc

        return _impl


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_returns_immediately_on_success() -> None:
    c = _Counter()
    result = await retry_async(
        c.succeed, is_transient=lambda _e: True, max_attempts=5, base_ms=1, cap_ms=1
    )
    assert result == "ok"
    assert c.calls == 1


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retries_then_succeeds_on_transient() -> None:
    c = _Counter()
    func = c.make_failer(fails=2, exc=RuntimeError("transient"))
    result = await retry_async(
        func,
        is_transient=lambda _e: True,
        max_attempts=5,
        base_ms=1,
        cap_ms=1,
    )
    assert result == "ok"
    assert c.calls == 3


@pytest.mark.asyncio
async def test_raises_after_exhausting_attempts() -> None:
    c = _Counter()
    func = c.make_always_failing(RuntimeError("nope"))
    with pytest.raises(RuntimeError, match="nope"):
        await retry_async(
            func,
            is_transient=lambda _e: True,
            max_attempts=3,
            base_ms=1,
            cap_ms=1,
        )
    assert c.calls == 3


@pytest.mark.asyncio
async def test_does_not_retry_when_not_transient() -> None:
    c = _Counter()
    func = c.make_always_failing(ValueError("permanent"))
    with pytest.raises(ValueError, match="permanent"):
        await retry_async(
            func,
            is_transient=lambda exc: not isinstance(exc, ValueError),
            max_attempts=10,
            base_ms=1,
            cap_ms=1,
        )
    assert c.calls == 1


# ---------------------------------------------------------------------------
# Edge: bounded attempt count
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_attempts_zero_is_coerced_to_one() -> None:
    c = _Counter()
    func = c.make_always_failing(RuntimeError("once"))
    with pytest.raises(RuntimeError):
        await retry_async(
            func,
            is_transient=lambda _e: True,
            max_attempts=0,
            base_ms=1,
            cap_ms=1,
        )
    assert c.calls == 1


@pytest.mark.asyncio
async def test_max_attempts_one_does_not_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    async def _spy_sleep(d: float) -> None:
        sleeps.append(d)

    monkeypatch.setattr("app.services.retry.asyncio.sleep", _spy_sleep)
    c = _Counter()
    with pytest.raises(RuntimeError):
        await retry_async(
            c.make_always_failing(RuntimeError("e")),
            is_transient=lambda _e: True,
            max_attempts=1,
            base_ms=10,
            cap_ms=10,
        )
    assert sleeps == []


# ---------------------------------------------------------------------------
# on_retry callback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_retry_invoked_with_attempt_exc_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _no_sleep(_d: float) -> None:
        return None

    monkeypatch.setattr("app.services.retry.asyncio.sleep", _no_sleep)
    monkeypatch.setattr("app.services.retry.random.uniform", lambda _a, _b: 0.0)

    seen: list[tuple[int, str, float]] = []

    def _on_retry(attempt: int, exc: BaseException, delay: float) -> None:
        seen.append((attempt, str(exc), delay))

    c = _Counter()
    func = c.make_failer(fails=2, exc=RuntimeError("oops"))
    result = await retry_async(
        func,
        is_transient=lambda _e: True,
        max_attempts=5,
        base_ms=100,
        cap_ms=10_000,
        on_retry=_on_retry,
    )
    assert result == "ok"
    # Two retries (attempts 1 and 2 each failed; succeeded on 3rd).
    assert [s[0] for s in seen] == [1, 2]
    # First retry: backoff = min(cap, base * 2**0) = 100ms => 0.1s.
    # Second retry: backoff = min(cap, base * 2**1) = 200ms => 0.2s.
    assert seen[0][2] == pytest.approx(0.1)
    assert seen[1][2] == pytest.approx(0.2)
    assert all("oops" in s[1] for s in seen)


@pytest.mark.asyncio
async def test_on_retry_exception_does_not_break_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _no_sleep(_d: float) -> None:
        return None

    monkeypatch.setattr("app.services.retry.asyncio.sleep", _no_sleep)

    def _bad_callback(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("logging exploded")

    c = _Counter()
    result = await retry_async(
        c.make_failer(fails=1, exc=RuntimeError("x")),
        is_transient=lambda _e: True,
        max_attempts=3,
        base_ms=1,
        cap_ms=1,
        on_retry=_bad_callback,
    )
    assert result == "ok"
    assert c.calls == 2


# ---------------------------------------------------------------------------
# Backoff capping
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backoff_is_capped_by_cap_ms(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force jitter == 0 and capture sleep durations.
    monkeypatch.setattr("app.services.retry.random.uniform", lambda _a, _b: 0.0)
    sleeps: list[float] = []

    async def _spy_sleep(d: float) -> None:
        sleeps.append(d)

    monkeypatch.setattr("app.services.retry.asyncio.sleep", _spy_sleep)

    c = _Counter()
    with pytest.raises(RuntimeError):
        await retry_async(
            c.make_always_failing(RuntimeError("e")),
            is_transient=lambda _e: True,
            max_attempts=6,
            base_ms=100,
            cap_ms=250,
        )
    # 5 retries before final raise; expected delays in seconds, capped at 0.25s.
    assert sleeps == [pytest.approx(0.1), pytest.approx(0.2), pytest.approx(0.25), pytest.approx(0.25), pytest.approx(0.25)]


@pytest.mark.asyncio
async def test_does_not_sleep_after_final_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    async def _spy_sleep(d: float) -> None:
        sleeps.append(d)

    monkeypatch.setattr("app.services.retry.asyncio.sleep", _spy_sleep)

    c = _Counter()
    with pytest.raises(RuntimeError):
        await retry_async(
            c.make_always_failing(RuntimeError("e")),
            is_transient=lambda _e: True,
            max_attempts=3,
            base_ms=1,
            cap_ms=1,
        )
    # 2 inter-attempt sleeps; never a sleep after the third (final) failure.
    assert len(sleeps) == 2


# ---------------------------------------------------------------------------
# Exception type preservation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preserves_original_exception_type() -> None:
    class CustomError(RuntimeError):
        pass

    c = _Counter()
    with pytest.raises(CustomError):
        await retry_async(
            c.make_always_failing(CustomError("boom")),
            is_transient=lambda _e: True,
            max_attempts=2,
            base_ms=1,
            cap_ms=1,
        )


@pytest.mark.asyncio
async def test_cancelled_error_propagates_when_not_transient() -> None:
    c = _Counter()

    async def _impl() -> str:
        c.calls += 1
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await retry_async(
            _impl,
            is_transient=lambda exc: not isinstance(exc, asyncio.CancelledError),
            max_attempts=5,
            base_ms=1,
            cap_ms=1,
        )
    assert c.calls == 1
