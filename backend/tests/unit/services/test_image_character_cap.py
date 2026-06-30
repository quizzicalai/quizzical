"""Character-image FAL fan-out cap + FAL spend recording (Hitlist #5 + #2).

``generate_character_images`` must slice the cast to ``quiz.max_character_images``
BEFORE the paid fan-out, and record the count of GENUINELY-PAID generations (not
cache hits) into the daily cents counter via cost_meter.
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
