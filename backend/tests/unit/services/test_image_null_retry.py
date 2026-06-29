# backend/tests/unit/services/test_image_null_retry.py
"""P1 image-pipeline reliability fixes.

Covers two audit findings:

1. NULL IMAGES NEVER RETRIED — when ``_client.generate`` returns ``None`` (FAL
   retries exhausted / NSFW redaction / empty result) the pipeline used to
   persist a permanent null. ``_generate_with_null_retry`` now re-issues the
   same prompt a bounded number of times before giving up (still fail-open).

2. CROSS-TOPIC CHARACTER-IMAGE BLEED — ``_get_character_url`` now filters on
   ``canonical_key`` so a different topic's identically named character cannot
   leak its cached art.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from app.models.db import Character
from app.services import image_pipeline as ip
from tests.fixtures.db_fixtures import sqlite_db_session  # noqa: F401

pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# (a) bounded null-retry: None then a URL -> retry yields the URL
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


# ---------------------------------------------------------------------------
# (b) canonical_key-scoped reuse: a row with a DIFFERENT canonical_key is
#     NOT reused for a same-named character.
# ---------------------------------------------------------------------------

@pytest.fixture
def _patch_session_ctx(monkeypatch):
    """Return a factory binding ``_db_session_ctx`` to a given AsyncSession."""

    def _bind(session):
        @asynccontextmanager
        async def _ctx():
            yield session

        monkeypatch.setattr(ip, "_db_session_ctx", _ctx, raising=False)

    return _bind


@pytest.mark.asyncio
async def test_get_character_url_does_not_reuse_other_canonical_key(
    sqlite_db_session, _patch_session_ctx
):
    """Seed "Fire" (canonical_key='fire-avatar'); a lookup scoped to a
    different canonical_key must NOT return that row's image_url."""
    seeded = Character(
        name="Fire",
        short_description="a fire bender",
        profile_text="a profile body",
        canonical_key="fire-avatar",
        image_url="https://v3.fal.media/avatar-fire.jpg",
    )
    sqlite_db_session.add(seeded)
    await sqlite_db_session.commit()

    _patch_session_ctx(sqlite_db_session)

    # Same display name, but a DIFFERENT canonical identity (e.g. another
    # topic's "Fire"). Must be a cache miss — no cross-topic bleed.
    miss = await ip._get_character_url("Fire", canonical_key="fire-naruto")
    assert miss is None

    # The matching canonical_key DOES reuse the cached art.
    hit = await ip._get_character_url("Fire", canonical_key="fire-avatar")
    assert hit == "https://v3.fal.media/avatar-fire.jpg"


@pytest.mark.asyncio
async def test_get_character_url_name_fallback_when_no_key(
    sqlite_db_session, _patch_session_ctx
):
    """When no canonical_key is supplied, the legacy name-only lookup applies
    so existing callers keep working (fail-safe)."""
    seeded = Character(
        name="The Leader",
        short_description="the leader of the pack",
        profile_text="a profile body",
        canonical_key="the leader",
        image_url="https://v3.fal.media/leader.jpg",
    )
    sqlite_db_session.add(seeded)
    await sqlite_db_session.commit()

    _patch_session_ctx(sqlite_db_session)

    hit = await ip._get_character_url("The Leader")
    assert hit == "https://v3.fal.media/leader.jpg"

    miss = await ip._get_character_url("Nobody")
    assert miss is None


@pytest.mark.asyncio
async def test_persist_character_url_scopes_where_by_canonical_key(monkeypatch):
    """``_persist_character_url`` targets the row by ``canonical_key`` when one
    is supplied (vs. the legacy ``name`` clause), so two topics that share a
    character name never overwrite each other's art.

    Uses a capturing fake session (avoids Postgres ``now()`` under SQLite).
    """
    captured: dict = {}

    class _Conn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def execute(self, stmt, params):
            captured["sql"] = str(stmt)
            captured["params"] = params

        async def commit(self):
            pass

    monkeypatch.setattr(ip, "_db_session_ctx", lambda: _Conn(), raising=False)

    await ip._persist_character_url(
        name="Spark", url="https://v3.fal.media/right.jpg",
        canonical_key="spark-pokemon",
    )
    sql = captured["sql"].lower()
    assert "update characters" in sql
    assert "where canonical_key = :ckey" in sql
    assert "where name" not in sql
    assert captured["params"] == {
        "url": "https://v3.fal.media/right.jpg", "ckey": "spark-pokemon"
    }


@pytest.mark.asyncio
async def test_persist_character_url_name_fallback_when_no_key(monkeypatch):
    """Without a canonical_key the legacy name-scoped UPDATE is used."""
    captured: dict = {}

    class _Conn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def execute(self, stmt, params):
            captured["sql"] = str(stmt)
            captured["params"] = params

        async def commit(self):
            pass

    monkeypatch.setattr(ip, "_db_session_ctx", lambda: _Conn(), raising=False)

    await ip._persist_character_url(name="Spark", url="https://v3.fal.media/r.jpg")
    sql = captured["sql"].lower()
    assert "where name = :name" in sql
    assert "canonical_key" not in sql
    assert captured["params"] == {"url": "https://v3.fal.media/r.jpg", "name": "Spark"}
