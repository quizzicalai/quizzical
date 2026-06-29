# backend/tests/unit/services/test_image_null_retry.py
"""P1 image-pipeline reliability fix: bounded null-image retry.

Audit finding — NULL IMAGES NEVER RETRIED: when ``_client.generate`` returns
``None`` (FAL internal retries exhausted / NSFW redaction / empty result) the
pipeline used to persist a permanent null. ``_generate_with_null_retry`` now
re-issues the same prompt a bounded number of times before giving up, while
keeping the existing fail-open semantics (still ``None`` after the budget,
never raises).
"""

from __future__ import annotations

import pytest

from app.services import image_pipeline as ip

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# None then a URL -> retry yields the URL
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_null_retry_recovers_after_none(monkeypatch):
    """First generate() returns None, the retry returns a URL."""
    calls = {"n": 0}

    async def _gen(prompt, **kw):
        calls["n"] += 1
        # None on the first attempt, a usable URL on the first re-issue.
        return None if calls["n"] == 1 else "https://v3.fal.media/ok.jpg"

    monkeypatch.setattr(ip._client, "generate", _gen, raising=False)
    # At least one extra attempt is allowed.
    monkeypatch.setattr(ip, "_null_retry_attempts", lambda: 2, raising=False)

    url = await ip._generate_with_null_retry("a brave knight", seed=7)

    assert url == "https://v3.fal.media/ok.jpg"
    assert calls["n"] == 2  # initial None + one successful re-issue


@pytest.mark.asyncio
async def test_null_retry_stops_at_first_url_no_extra_calls(monkeypatch):
    """A URL on the very first call short-circuits — no retries issued."""
    calls = {"n": 0}

    async def _gen(prompt, **kw):
        calls["n"] += 1
        return "https://v3.fal.media/first.jpg"

    monkeypatch.setattr(ip._client, "generate", _gen, raising=False)
    monkeypatch.setattr(ip, "_null_retry_attempts", lambda: 2, raising=False)

    url = await ip._generate_with_null_retry("a wise mentor", seed=1)

    assert url == "https://v3.fal.media/first.jpg"
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_null_retry_exhausted_stays_none_failopen(monkeypatch):
    """Every attempt returns None -> result is None and call count is bounded.

    Total generate() calls == 1 + _null_retry_attempts(); never raises.
    """
    calls = {"n": 0}

    async def _gen(prompt, **kw):
        calls["n"] += 1
        return None

    monkeypatch.setattr(ip._client, "generate", _gen, raising=False)
    monkeypatch.setattr(ip, "_null_retry_attempts", lambda: 2, raising=False)

    url = await ip._generate_with_null_retry("an empty prompt", seed=3)

    assert url is None
    assert calls["n"] == 3  # 1 initial + 2 retries


@pytest.mark.asyncio
async def test_null_retry_disabled_does_not_re_issue(monkeypatch):
    """When the retry budget is 0, behaviour matches legacy single-shot."""
    calls = {"n": 0}

    async def _gen(prompt, **kw):
        calls["n"] += 1
        return None

    monkeypatch.setattr(ip._client, "generate", _gen, raising=False)
    monkeypatch.setattr(ip, "_null_retry_attempts", lambda: 0, raising=False)

    url = await ip._generate_with_null_retry("x", seed=0)

    assert url is None
    assert calls["n"] == 1


def test_null_retry_attempts_clamped(monkeypatch):
    """Config-derived attempt count is clamped to the small upper bound."""
    class _Retry:
        max_attempts = 999

    class _Cfg:
        retry = _Retry()

    monkeypatch.setattr(ip, "_img_cfg", lambda: _Cfg(), raising=False)
    assert ip._null_retry_attempts() == ip._MAX_NULL_RETRY_ATTEMPTS

    # max_attempts == 1 means "no retries" (the first try is attempt 1).
    class _Retry1:
        max_attempts = 1

    class _Cfg1:
        retry = _Retry1()

    monkeypatch.setattr(ip, "_img_cfg", lambda: _Cfg1(), raising=False)
    assert ip._null_retry_attempts() == 0
