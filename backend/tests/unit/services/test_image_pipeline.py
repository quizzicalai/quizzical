# tests/unit/services/test_image_pipeline.py
"""Tests for the FAL image orchestration pipeline (§7.8 / AC-IMG-6..10)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.no_tool_stubs]


@pytest.fixture(autouse=True)
def _default_no_cache(monkeypatch):
    """Default every test to a cache-miss + dead-URL state.

    Tests that exercise the cache-hit fast path override these stubs.
    Without this, every test would have to remember to patch the batched
    cache lookup (``_get_character_urls``) / ``_url_alive`` to avoid the
    short-circuit introduced for runtime image consistency.

    Hitlist #14 (2026-06-30): the pipeline now resolves the cache via a SINGLE
    batched ``_get_character_urls`` query (and persists via the batched
    ``_persist_character_urls_batch`` / ``_refresh_character_set_images_batch``)
    instead of the per-character N+1 helpers. Patch the batched surface here.
    """
    from app.services import image_pipeline as ip
    monkeypatch.setattr(
        ip, "_get_character_urls",
        AsyncMock(side_effect=lambda names: dict.fromkeys(names)),
        raising=False,
    )
    monkeypatch.setattr(ip, "_url_alive", AsyncMock(return_value=False), raising=False)
    monkeypatch.setattr(ip, "_persist_character_urls_batch", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(ip, "_refresh_character_set_images_batch", AsyncMock(return_value=None), raising=False)


@pytest.fixture
def chars():
    from app.models.api import CharacterProfile
    return [
        CharacterProfile(name="Alpha", short_description="brave warrior", profile_text="..."),
        CharacterProfile(name="Beta", short_description="quiet scholar", profile_text="..."),
        CharacterProfile(name="Gamma", short_description="cheerful trickster", profile_text="..."),
    ]


# AC-IMG-6: returns mapping including failures
@pytest.mark.asyncio
async def test_generate_character_images_returns_mapping_for_all(monkeypatch, chars):
    from app.services import image_pipeline as ip

    seq = iter(["https://x/1.jpg", None, "https://x/3.jpg"])
    async def _gen(prompt, **kw):
        return next(seq)

    # Disable the P1 bounded null-retry for this test so there is exactly one
    # generate() call per character and the fixed 3-element sequence maps
    # 1:1 onto Alpha/Beta/Gamma (Beta -> None). The retry behaviour itself is
    # covered by test_image_null_retry.py.
    monkeypatch.setattr(ip, "_null_retry_attempts", lambda: 0, raising=False)
    monkeypatch.setattr(ip._client, "generate", _gen, raising=False)
    monkeypatch.setattr(ip, "_persist_character_url", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(ip, "_refresh_character_set_image", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(ip, "_enabled", lambda: True, raising=False)

    out = await ip.generate_character_images(
        session_id=uuid4(), characters=chars, category="X", analysis={"is_media": False},
    )
    assert set(out.keys()) == {"Alpha", "Beta", "Gamma"}
    assert out["Alpha"] == "https://x/1.jpg"
    assert out["Beta"] is None
    assert out["Gamma"] == "https://x/3.jpg"


# AC-IMG-6: bounded by semaphore
@pytest.mark.asyncio
async def test_generate_character_images_respects_concurrency(monkeypatch, chars):
    from app.services import image_pipeline as ip

    in_flight = 0
    peak = 0

    async def _gen(prompt, **kw):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        return "https://x/y.jpg"

    monkeypatch.setattr(ip._client, "generate", _gen, raising=False)
    monkeypatch.setattr(ip, "_persist_character_url", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(ip, "_refresh_character_set_image", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(ip, "_get_concurrency", lambda: 2, raising=False)
    monkeypatch.setattr(ip, "_enabled", lambda: True, raising=False)

    big = chars * 3  # 9 tasks
    out = await ip.generate_character_images(
        session_id=uuid4(), characters=big, category="X", analysis={},
    )
    assert len(out) == 3  # dedup by name
    assert peak <= 2


# AC-IMG-7 (updated): persist_character_url now unconditionally refreshes the
# row. The previous IS NULL guard prevented re-imports from refreshing stale
# precomputed URLs (Star Wars regression, 2026-05-16). The new contract is:
# whoever calls persist has the freshest known-good URL.
@pytest.mark.asyncio
async def test_persist_character_url_overwrites_existing(monkeypatch):
    from app.services import image_pipeline as ip

    captured = {}

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

    await ip._persist_character_url(name="Alpha", url="https://x/y.jpg")
    sql = captured["sql"].lower()
    assert "update characters" in sql
    # The whole point of this change -- no NULL guard.
    assert "image_url is null" not in sql
    assert captured["params"] == {"url": "https://x/y.jpg", "name": "Alpha"}


# Cache-hit fast path: if the DB already has a URL and HEAD succeeds, no FAL.
@pytest.mark.asyncio
async def test_generate_character_images_cache_hit_skips_fal(monkeypatch, chars):
    from app.services import image_pipeline as ip

    fal_calls = 0
    async def _gen(prompt, **kw):
        nonlocal fal_calls
        fal_calls += 1
        return "https://x/new.jpg"

    cached_urls = {
        "Alpha": "https://cdn/old-alpha.jpg",
        "Beta": "https://cdn/old-beta.jpg",
        "Gamma": "https://cdn/old-gamma.jpg",
    }
    refreshed = []

    async def _get_urls(names):
        return {n: cached_urls.get(n) for n in names}

    async def _alive(url, **kw):
        return True

    async def _refresh_batch(*, session_id, items):
        refreshed.extend(items)

    monkeypatch.setattr(ip._client, "generate", _gen, raising=False)
    monkeypatch.setattr(ip, "_persist_character_urls_batch", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(ip, "_refresh_character_set_images_batch", _refresh_batch, raising=False)
    monkeypatch.setattr(ip, "_get_character_urls", _get_urls, raising=False)
    monkeypatch.setattr(ip, "_url_alive", _alive, raising=False)
    monkeypatch.setattr(ip, "_enabled", lambda: True, raising=False)

    out = await ip.generate_character_images(
        session_id=uuid4(), characters=chars, category="Star Wars",
        analysis={"is_media": True},
    )

    assert fal_calls == 0, "FAL must not be called when cache hits"
    assert out == cached_urls
    assert sorted(refreshed) == sorted(cached_urls.items())


# Cache-miss when HEAD fails -> regenerates and persists.
@pytest.mark.asyncio
async def test_generate_character_images_dead_url_regenerates(monkeypatch, chars):
    from app.services import image_pipeline as ip

    async def _gen(prompt, **kw):
        return "https://x/fresh.jpg"

    async def _get_urls(names):
        return dict.fromkeys(names, "https://dead/old.jpg")

    async def _alive(url, **kw):
        return False  # dead

    persisted = []
    async def _persist_batch(items):
        persisted.extend(items)

    monkeypatch.setattr(ip._client, "generate", _gen, raising=False)
    monkeypatch.setattr(ip, "_persist_character_urls_batch", _persist_batch, raising=False)
    monkeypatch.setattr(ip, "_refresh_character_set_images_batch", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(ip, "_get_character_urls", _get_urls, raising=False)
    monkeypatch.setattr(ip, "_url_alive", _alive, raising=False)
    monkeypatch.setattr(ip, "_enabled", lambda: True, raising=False)

    out = await ip.generate_character_images(
        session_id=uuid4(), characters=chars[:1], category="X", analysis={},
    )
    assert out == {"Alpha": "https://x/fresh.jpg"}
    assert persisted == [("Alpha", "https://x/fresh.jpg")]


# AC-IMG-9: result image safe no-op when final_result is NULL
@pytest.mark.asyncio
async def test_generate_result_image_is_safe_when_final_result_missing(monkeypatch):
    from app.models.api import FinalResult
    from app.services import image_pipeline as ip

    monkeypatch.setattr(ip._client, "generate",
                        AsyncMock(return_value="https://x/r.jpg"), raising=False)
    persisted = AsyncMock(return_value=None)
    monkeypatch.setattr(ip, "_persist_result_image", persisted, raising=False)
    monkeypatch.setattr(ip, "_enabled", lambda: True, raising=False)

    out = await ip.generate_result_image(
        session_id=uuid4(),
        result=FinalResult(title="t", description="d"),
        category="C", character_set=[],
    )
    assert out == "https://x/r.jpg"
    persisted.assert_awaited_once()


# AC-IMG-ASPECT-1: synopsis hero requested at 16:9 landscape
@pytest.mark.asyncio
async def test_generate_synopsis_image_requests_landscape(monkeypatch):
    from app.models.api import Synopsis
    from app.services import image_pipeline as ip

    captured: dict = {}

    async def _gen(prompt, **kw):
        captured.update(kw)
        return "https://x/s.jpg"

    monkeypatch.setattr(ip._client, "generate", _gen, raising=False)
    monkeypatch.setattr(ip, "_persist_synopsis_image", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(ip, "_enabled", lambda: True, raising=False)

    out = await ip.generate_synopsis_image(
        session_id=uuid4(),
        synopsis=Synopsis(title="t", summary="s"),
        category="C",
        analysis={},
    )
    assert out == "https://x/s.jpg"
    assert captured.get("image_size") == {"width": 1024, "height": 576}


# AC-IMG-ASPECT-3: result hero requested at 1:1 square (FE renders with
# `aspect-square`; landscape used to crop the matched character awkwardly).
@pytest.mark.asyncio
async def test_generate_result_image_requests_square(monkeypatch):
    from app.models.api import FinalResult
    from app.services import image_pipeline as ip

    captured: dict = {}

    async def _gen(prompt, **kw):
        captured.update(kw)
        return "https://x/r.jpg"

    monkeypatch.setattr(ip._client, "generate", _gen, raising=False)
    monkeypatch.setattr(ip, "_persist_result_image", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(ip, "_enabled", lambda: True, raising=False)

    out = await ip.generate_result_image(
        session_id=uuid4(),
        result=FinalResult(title="t", description="d"),
        category="C", character_set=[],
    )
    assert out == "https://x/r.jpg"
    assert captured.get("image_size") == {"width": 1024, "height": 1024}


# Branded-topic ladder — rung 1 ("<name> from <source>") succeeds, the
# LLM describer is never called. Verifies that ``analysis.is_media=True``
# routes through the new fallback ladder and that the literal name+source
# is what reaches FAL.
@pytest.mark.asyncio
async def test_generate_character_images_branded_rung1_succeeds(monkeypatch, chars):
    from app.services import image_pipeline as ip

    seen_prompts: list[str] = []

    async def _gen(prompt, **kw):
        seen_prompts.append(prompt)
        return "https://fal.media/x/ok.jpg"

    async def _describe(**kw):
        raise AssertionError("describe should not be called when rung 1 succeeds")

    monkeypatch.setattr(ip._client, "generate", _gen, raising=False)
    monkeypatch.setattr(
        "app.services.character_describer.describe_character_physically",
        _describe,
    )
    monkeypatch.setattr(ip, "_persist_character_url", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(ip, "_refresh_character_set_image", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(ip, "_enabled", lambda: True, raising=False)

    out = await ip.generate_character_images(
        session_id=uuid4(),
        characters=chars[:1],   # one char is enough; just verify the ladder shape
        category="Harry Potter",
        analysis={"is_media": True},
    )
    assert out == {"Alpha": "https://fal.media/x/ok.jpg"}    # Literal "<name> from <source>" present in the prompt we sent to FAL.
    assert any("Alpha from Harry Potter" in p for p in seen_prompts)


# Branded-topic ladder — rung 1 returns None (safety/IP refusal), rung 2
# (LLM-described, no branded items) succeeds. Verifies the describer is
# called exactly once and FAL is called twice.
@pytest.mark.asyncio
async def test_generate_character_images_branded_rung2_succeeds(monkeypatch, chars):
    from app.services import image_pipeline as ip

    fal_calls: list[str] = []

    async def _gen(prompt, **kw):
        fal_calls.append(prompt)
        return None if len(fal_calls) == 1 else "https://fal.media/x/ok2.jpg"

    describe_calls: list[dict] = []

    async def _describe(*, name, source, strict_level=0, **kw):
        describe_calls.append({"name": name, "source": source, "strict_level": strict_level})
        return "A tall figure with long dark hair and weathered armour."

    # Disable the P1 null-retry so each rung issues exactly one FAL call and the
    # rung-stepping behaviour is what's asserted (not the retry count).
    monkeypatch.setattr(ip, "_null_retry_attempts", lambda: 0, raising=False)
    monkeypatch.setattr(ip._client, "generate", _gen, raising=False)
    monkeypatch.setattr(
        "app.services.character_describer.describe_character_physically",
        _describe,
    )
    monkeypatch.setattr(ip, "_persist_character_url", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(ip, "_refresh_character_set_image", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(ip, "_enabled", lambda: True, raising=False)

    out = await ip.generate_character_images(
        session_id=uuid4(),
        characters=chars[:1],
        category="The Lord of the Rings",
        analysis={"is_media": True},
    )
    assert out == {"Alpha": "https://fal.media/x/ok2.jpg"}
    assert len(fal_calls) == 2
    assert len(describe_calls) == 1
    assert describe_calls[0]["strict_level"] == 0
    assert describe_calls[0]["source"] == "The Lord of the Rings"
    # Rung 2 prompt should NOT contain "from <source>" (we're using the
    # sanitized LLM description, not the literal name).
    assert "from The Lord of the Rings" not in fal_calls[1]


# Branded-topic ladder — all three rungs return None, helper returns None
# and the mapping carries a None for that character.
@pytest.mark.asyncio
async def test_generate_character_images_branded_all_rungs_fail(monkeypatch, chars):
    from app.services import image_pipeline as ip

    fal_calls = 0

    async def _gen(prompt, **kw):
        nonlocal fal_calls
        fal_calls += 1
        return None

    describe_calls: list[int] = []

    async def _describe(*, name, source, strict_level=0, **kw):
        describe_calls.append(strict_level)
        return "An ordinary person in plain clothing."

    # Disable the P1 null-retry so the ladder issues exactly one FAL call per
    # rung (3 total) rather than re-issuing each rung on its None result.
    monkeypatch.setattr(ip, "_null_retry_attempts", lambda: 0, raising=False)
    monkeypatch.setattr(ip._client, "generate", _gen, raising=False)
    monkeypatch.setattr(
        "app.services.character_describer.describe_character_physically",
        _describe,
    )
    monkeypatch.setattr(ip, "_persist_character_url", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(ip, "_refresh_character_set_image", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(ip, "_enabled", lambda: True, raising=False)

    out = await ip.generate_character_images(
        session_id=uuid4(),
        characters=chars[:1],
        category="Star Wars",
        analysis={"is_media": True},
    )
    assert out == {"Alpha": None}
    assert fal_calls == 3
    assert describe_calls == [0, 1]
