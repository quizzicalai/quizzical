# tests/unit/services/test_image_pipeline.py
"""Tests for the FAL image orchestration pipeline (§7.8 / AC-IMG-6..10)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.no_tool_stubs]


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


# AC-IMG-7: only updates when image_url IS NULL → uses guarded SQL
@pytest.mark.asyncio
async def test_persist_character_url_uses_null_guard(monkeypatch):
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
    assert "image_url is null" in sql


# AC-IMG-9: result image safe no-op when final_result is NULL
@pytest.mark.asyncio
async def test_generate_result_image_is_safe_when_final_result_missing(monkeypatch):
    from app.services import image_pipeline as ip
    from app.models.api import FinalResult

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
    from app.services import image_pipeline as ip
    from app.models.api import Synopsis

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


# AC-IMG-ASPECT-2: result hero requested at 16:9 landscape
@pytest.mark.asyncio
async def test_generate_result_image_requests_landscape(monkeypatch):
    from app.services import image_pipeline as ip
    from app.models.api import FinalResult

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
    assert captured.get("image_size") == {"width": 1024, "height": 576}
