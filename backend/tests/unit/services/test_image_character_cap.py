"""Character-image FAL fan-out cap + FAL spend recording (Hitlist #5 + #2).

``generate_character_images`` must:
  * cap only the NEW (uncached / paid) generations at ``quiz.max_character_images``
    — never drop already-CACHED (free) thumbnails (review item B);
  * record the ACTUAL number of ``_client.generate`` calls (including null-retries
    and brand-ladder rungs), not "1 per character" (review item A);
  * never count cache hits as paid spend.
"""
from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.no_tool_stubs]


def _make_chars(n: int):
    from app.models.api import CharacterProfile
    return [
        CharacterProfile(name=f"C{i}", short_description="d", profile_text="...")
        for i in range(n)
    ]


@pytest.fixture(autouse=True)
def _no_cache(monkeypatch):
    from app.services import image_pipeline as ip
    monkeypatch.setattr(ip, "_get_character_url", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(ip, "_url_alive", AsyncMock(return_value=False), raising=False)
    monkeypatch.setattr(ip, "_persist_character_url", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(ip, "_refresh_character_set_image", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(ip, "_null_retry_attempts", lambda: 0, raising=False)
    monkeypatch.setattr(ip, "_enabled", lambda: True, raising=False)


@pytest.mark.asyncio
async def test_fanout_is_capped_at_max_character_images(monkeypatch):
    from app.services import image_pipeline as ip

    calls = {"n": 0}

    async def _gen(prompt, **kw):
        calls["n"] += 1
        return "https://fal.media/x.jpg"

    monkeypatch.setattr(ip._client, "generate", _gen, raising=False)
    monkeypatch.setattr(ip, "_max_character_images", lambda: 5, raising=False)
    recorded = {"paid": None}

    async def _rec(n_images):
        recorded["paid"] = n_images

    monkeypatch.setattr(
        "app.services.cost_meter.record_fal_image_cost", _rec, raising=True
    )

    chars = _make_chars(20)  # 20 unique, cap 5
    out = await ip.generate_character_images(
        session_id=uuid4(), characters=chars, category="X", analysis={"is_media": False},
    )

    assert calls["n"] == 5, "only the first 5 of the cast should incur a paid FAL call"
    assert len(out) == 5
    assert set(out.keys()) == {f"C{i}" for i in range(5)}
    assert recorded["paid"] == 5  # paid-generation count recorded into the breaker


@pytest.mark.asyncio
async def test_cap_zero_disables_cap(monkeypatch):
    from app.services import image_pipeline as ip

    calls = {"n": 0}

    async def _gen(prompt, **kw):
        calls["n"] += 1
        return "https://fal.media/x.jpg"

    monkeypatch.setattr(ip._client, "generate", _gen, raising=False)
    monkeypatch.setattr(ip, "_max_character_images", lambda: 0, raising=False)  # disabled
    monkeypatch.setattr(
        "app.services.cost_meter.record_fal_image_cost", AsyncMock(), raising=True
    )

    chars = _make_chars(8)
    out = await ip.generate_character_images(
        session_id=uuid4(), characters=chars, category="X", analysis={"is_media": False},
    )
    assert calls["n"] == 8 and len(out) == 8  # uncapped


@pytest.mark.asyncio
async def test_cache_hits_not_counted_as_paid(monkeypatch):
    """A character whose art already exists (cache hit) makes no FAL call and is
    not billed; only genuine generations count toward FAL spend."""
    from app.services import image_pipeline as ip

    # First two characters are cache hits (live URL), the rest miss.
    def _existing(name):
        return "https://fal.media/cached.jpg" if name in ("C0", "C1") else None

    monkeypatch.setattr(ip, "_get_character_url", AsyncMock(side_effect=_existing), raising=False)
    monkeypatch.setattr(ip, "_url_alive", AsyncMock(return_value=True), raising=False)

    gen_calls = {"n": 0}

    async def _gen(prompt, **kw):
        gen_calls["n"] += 1
        return "https://fal.media/new.jpg"

    monkeypatch.setattr(ip._client, "generate", _gen, raising=False)
    monkeypatch.setattr(ip, "_max_character_images", lambda: 0, raising=False)
    recorded = {"paid": None}

    async def _rec(n_images):
        recorded["paid"] = n_images

    monkeypatch.setattr(
        "app.services.cost_meter.record_fal_image_cost", _rec, raising=True
    )

    chars = _make_chars(5)  # C0,C1 cached; C2,C3,C4 generated
    out = await ip.generate_character_images(
        session_id=uuid4(), characters=chars, category="X", analysis={"is_media": False},
    )
    assert len(out) == 5
    assert gen_calls["n"] == 3
    assert recorded["paid"] == 3  # only the 3 paid generations are billed


@pytest.mark.asyncio
async def test_metering_fault_does_not_break_pipeline(monkeypatch):
    """A cost_meter exception must never affect the image pipeline (fail-open)."""
    from app.services import image_pipeline as ip

    async def _gen(prompt, **kw):
        return "https://fal.media/x.jpg"

    async def _boom(n_images):
        raise RuntimeError("meter down")

    monkeypatch.setattr(ip._client, "generate", _gen, raising=False)
    monkeypatch.setattr(ip, "_max_character_images", lambda: 0, raising=False)
    monkeypatch.setattr(
        "app.services.cost_meter.record_fal_image_cost", _boom, raising=True
    )

    out = await ip.generate_character_images(
        session_id=uuid4(), characters=_make_chars(3), category="X",
        analysis={"is_media": False},
    )
    assert len(out) == 3  # pipeline still returns its mapping despite the meter fault


# ===========================================================================
# Review item A — FAL spend is metered by ACTUAL generate() call count, not by
# character count. Null-retries and brand-ladder rungs each bill a call.
# ===========================================================================
@pytest.mark.asyncio
async def test_billing_counts_actual_generate_calls_with_null_retries(monkeypatch):
    """A non-branded character whose first generate() returns None and succeeds on
    a retry bills 2 calls (1 initial + 1 retry), not 1."""
    from app.services import image_pipeline as ip

    seq = {"n": 0}

    async def _gen(prompt, **kw):
        seq["n"] += 1
        # First call None, second call a URL  (per single character — only one
        # character in this test, so the sequence maps 1:1).
        return None if seq["n"] == 1 else "https://fal.media/ok.jpg"

    monkeypatch.setattr(ip._client, "generate", _gen, raising=False)
    monkeypatch.setattr(ip, "_null_retry_attempts", lambda: 2, raising=False)
    monkeypatch.setattr(ip, "_max_character_images", lambda: 0, raising=False)
    recorded = {"calls": None}

    async def _rec(n_images):
        recorded["calls"] = n_images

    monkeypatch.setattr(
        "app.services.cost_meter.record_fal_image_cost", _rec, raising=True
    )

    out = await ip.generate_character_images(
        session_id=uuid4(), characters=_make_chars(1), category="X",
        analysis={"is_media": False},
    )
    assert out["C0"] == "https://fal.media/ok.jpg"
    assert seq["n"] == 2  # 1 initial None + 1 retry == 2 billable generate() calls
    assert recorded["calls"] == 2, "metering must reflect ACTUAL FAL calls, not 1/char"


@pytest.mark.asyncio
async def test_branded_ladder_bills_every_rung_call(monkeypatch):
    """A branded character that exhausts rung 1 (1 + retries) and succeeds on
    rung 2 bills the sum of all generate() calls across rungs."""
    from app.services import image_pipeline as ip

    # Rung1 prompt always None; rung2 (descriptive) prompt returns a URL. We
    # distinguish by prompt content via the image_tools builders, so just count
    # calls and return None for the first 2 (rung1 initial + rung1 retry), URL
    # on the 3rd (rung2 initial).
    seq = {"n": 0}

    async def _gen(prompt, **kw):
        seq["n"] += 1
        return None if seq["n"] <= 2 else "https://fal.media/brand.jpg"

    monkeypatch.setattr(ip._client, "generate", _gen, raising=False)
    monkeypatch.setattr(ip, "_null_retry_attempts", lambda: 1, raising=False)  # rung1: 1+1=2
    monkeypatch.setattr(ip, "_max_character_images", lambda: 0, raising=False)

    # Stub the LLM description so rung2/rung3 have a prompt to render.
    async def _describe(name, source, strict_level):
        return f"a person resembling {name}"

    monkeypatch.setattr(
        "app.services.character_describer.describe_character_physically",
        _describe, raising=True,
    )
    recorded = {"calls": None}

    async def _rec(n_images):
        recorded["calls"] = n_images

    monkeypatch.setattr(
        "app.services.cost_meter.record_fal_image_cost", _rec, raising=True
    )

    out = await ip.generate_character_images(
        session_id=uuid4(), characters=_make_chars(1), category="The Office",
        analysis={"is_media": True},  # branded -> brand-fallback ladder
    )
    assert out["C0"] == "https://fal.media/brand.jpg"
    # rung1: 2 calls (initial + 1 null-retry), rung2: 1 call (success) == 3 total.
    assert seq["n"] == 3
    assert recorded["calls"] == 3, "every rung's FAL call must be billed"


# ===========================================================================
# Review item B — the cap limits only NEW generations; already-CACHED (free)
# thumbnails are NEVER dropped. A precompute pack with >cap cached characters
# renders the FULL cast uncapped.
# ===========================================================================
@pytest.mark.asyncio
async def test_cap_never_drops_cached_thumbnails(monkeypatch):
    from app.services import image_pipeline as ip

    # All 20 characters are cache hits (precomputed pack on cold start).
    monkeypatch.setattr(
        ip, "_get_character_url",
        AsyncMock(side_effect=lambda name: f"https://fal.media/{name}.jpg"),
        raising=False,
    )
    monkeypatch.setattr(ip, "_url_alive", AsyncMock(return_value=True), raising=False)

    gen_calls = {"n": 0}

    async def _gen(prompt, **kw):
        gen_calls["n"] += 1
        return "https://fal.media/new.jpg"

    monkeypatch.setattr(ip._client, "generate", _gen, raising=False)
    monkeypatch.setattr(ip, "_max_character_images", lambda: 5, raising=False)  # small cap
    recorded = {"calls": 0}

    async def _rec(n_images):
        recorded["calls"] = n_images

    monkeypatch.setattr(
        "app.services.cost_meter.record_fal_image_cost", _rec, raising=True
    )

    chars = _make_chars(20)  # 20 CACHED, cap 5
    out = await ip.generate_character_images(
        session_id=uuid4(), characters=chars, category="X", analysis={"is_media": False},
    )
    # The FULL cast renders despite cap=5 — cached thumbs are never capped.
    assert len(out) == 20
    assert set(out.keys()) == {f"C{i}" for i in range(20)}
    assert gen_calls["n"] == 0  # no paid generation; all served from cache
    assert recorded["calls"] == 0


@pytest.mark.asyncio
async def test_cap_applies_only_to_new_generations_mixed(monkeypatch):
    """Mixed set: >cap cached + several misses. ALL cached render; misses are
    capped to ``max_character_images``."""
    from app.services import image_pipeline as ip

    # C0..C7 cached, C8..C15 miss (8 misses), cap=3 -> 3 new generations only.
    def _existing(name):
        idx = int(name[1:])
        return f"https://fal.media/{name}.jpg" if idx < 8 else None

    monkeypatch.setattr(ip, "_get_character_url", AsyncMock(side_effect=_existing), raising=False)

    async def _alive(url):
        return True

    monkeypatch.setattr(ip, "_url_alive", AsyncMock(side_effect=_alive), raising=False)

    gen_calls = {"n": 0}

    async def _gen(prompt, **kw):
        gen_calls["n"] += 1
        return "https://fal.media/new.jpg"

    monkeypatch.setattr(ip._client, "generate", _gen, raising=False)
    monkeypatch.setattr(ip, "_null_retry_attempts", lambda: 0, raising=False)
    monkeypatch.setattr(ip, "_max_character_images", lambda: 3, raising=False)
    recorded = {"calls": None}

    async def _rec(n_images):
        recorded["calls"] = n_images

    monkeypatch.setattr(
        "app.services.cost_meter.record_fal_image_cost", _rec, raising=True
    )

    chars = _make_chars(16)
    out = await ip.generate_character_images(
        session_id=uuid4(), characters=chars, category="X", analysis={"is_media": False},
    )
    # 8 cached always present; 3 of the 8 misses generated.
    cached = {f"C{i}" for i in range(8)}
    assert cached.issubset(set(out.keys()))
    assert gen_calls["n"] == 3  # only the capped misses generated
    assert recorded["calls"] == 3
    # 8 cached + 3 generated == 11 entries in the result.
    assert len(out) == 11
