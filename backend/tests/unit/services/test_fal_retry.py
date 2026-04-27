# backend/tests/unit/services/test_fal_retry.py
"""§16.2 — AC-IMG-RETRY-1..4: FAL transient-error retry."""
from __future__ import annotations

import asyncio
from typing import Any, List

import pytest

from app.core.config import settings
from app.services import image_service as img_mod
from app.services import retry as retry_mod


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _sleep(_s: float) -> None:
        return None

    monkeypatch.setattr(retry_mod.asyncio, "sleep", _sleep)


@pytest.fixture(autouse=True)
def _enable_fal(monkeypatch):
    """Force enabled=True so generate() doesn't short-circuit. The autouse
    test fixture _disable_fal_image_gen sets enabled=False globally."""
    cfg = getattr(settings, "image_gen", None)
    monkeypatch.setattr(cfg, "enabled", True)
    monkeypatch.setenv("FAL_KEY", "test-key")
    # Override the conftest-installed False stub.
    monkeypatch.setattr(img_mod, "_image_gen_enabled", lambda: True, raising=False)


@pytest.fixture
def fal_calls(monkeypatch):
    """Patch fal_client.subscribe_async with a controllable stub."""
    state: dict[str, Any] = {"calls": 0, "raises": [], "ok_resp": None}

    async def _fake_subscribe(model: str, *, arguments: dict):
        state["calls"] += 1
        if state["raises"]:
            exc = state["raises"].pop(0)
            if exc is not None:
                raise exc
        return state["ok_resp"]

    monkeypatch.setattr(img_mod.fal_client, "subscribe_async", _fake_subscribe)
    return state


# ---------------------------------------------------------------------------
# AC-IMG-RETRY-1: transient errors retried up to max_attempts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc",
    [
        asyncio.TimeoutError("timeout"),
        ConnectionError("conn refused"),
        OSError("network unreachable"),
        Exception("HTTP 429 Too Many Requests"),
        Exception("upstream returned 503"),
        Exception("fal: rate-limit exceeded"),
        Exception("read timeout"),
    ],
)
async def test_fal_retry_recovers_after_transient_error(monkeypatch, fal_calls, exc):
    monkeypatch.setattr(settings.image_gen.retry, "max_attempts", 2)
    monkeypatch.setattr(settings.image_gen.retry, "base_ms", 1)
    monkeypatch.setattr(settings.image_gen.retry, "cap_ms", 2)
    fal_calls["raises"] = [exc]
    fal_calls["ok_resp"] = {"images": [{"url": "https://v3.fal.media/img.png"}]}

    client = img_mod.FalImageClient()
    url = await client.generate("a happy character")

    assert url == "https://v3.fal.media/img.png"
    assert fal_calls["calls"] == 2


# ---------------------------------------------------------------------------
# AC-IMG-RETRY-2: never raises; returns None when all retries exhausted
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fal_retry_exhausted_returns_none(monkeypatch, fal_calls):
    monkeypatch.setattr(settings.image_gen.retry, "max_attempts", 3)
    monkeypatch.setattr(settings.image_gen.retry, "base_ms", 1)
    monkeypatch.setattr(settings.image_gen.retry, "cap_ms", 2)
    fal_calls["raises"] = [
        ConnectionError("first"),
        ConnectionError("second"),
        ConnectionError("third"),
    ]

    client = img_mod.FalImageClient()
    url = await client.generate("subject")

    assert url is None
    assert fal_calls["calls"] == 3


# ---------------------------------------------------------------------------
# AC-IMG-RETRY-3: non-transient exceptions bypass retry; return None
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fal_retry_non_transient_no_retry(monkeypatch, fal_calls):
    monkeypatch.setattr(settings.image_gen.retry, "max_attempts", 5)
    monkeypatch.setattr(settings.image_gen.retry, "base_ms", 1)
    monkeypatch.setattr(settings.image_gen.retry, "cap_ms", 2)
    # ValueError is not in the transient class set and message has no
    # retriable keyword → must NOT retry.
    fal_calls["raises"] = [ValueError("bad prompt schema"), ValueError("again")]

    client = img_mod.FalImageClient()
    url = await client.generate("x")

    assert url is None
    assert fal_calls["calls"] == 1


# ---------------------------------------------------------------------------
# AC-IMG-RETRY-4: max_attempts=1 disables retry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fal_retry_disabled(monkeypatch, fal_calls):
    monkeypatch.setattr(settings.image_gen.retry, "max_attempts", 1)
    fal_calls["raises"] = [ConnectionError("once"), ConnectionError("twice")]

    client = img_mod.FalImageClient()
    url = await client.generate("y")

    assert url is None
    assert fal_calls["calls"] == 1


# ---------------------------------------------------------------------------
# Sanity: classifier
# ---------------------------------------------------------------------------

def test_is_fal_transient_classifier():
    cls = img_mod._is_fal_transient
    assert cls(asyncio.TimeoutError()) is True
    assert cls(ConnectionError()) is True
    assert cls(OSError("disk full")) is True  # OSError class match
    assert cls(Exception("HTTP 429 from upstream")) is True
    assert cls(Exception("got 502 bad gateway")) is True
    assert cls(Exception("connection reset by peer")) is True
    assert cls(ValueError("bad arg")) is False
    assert cls(Exception("validation failed")) is False
