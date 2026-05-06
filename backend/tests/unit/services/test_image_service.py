# tests/unit/services/test_image_service.py
"""Tests for FAL image client (§7.8 / AC-IMG-1..2)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.no_tool_stubs]


@pytest.fixture
def client(monkeypatch):
    from app.services import image_service as svc
    from app.services.image_service import FalImageClient
    # Override the autouse `_disable_fal_image_gen` for this file.
    monkeypatch.setattr(svc, "_image_gen_enabled", lambda: True, raising=False)
    return FalImageClient()


# AC-IMG-1: returns None on exception
@pytest.mark.asyncio
async def test_generate_returns_none_when_fal_raises(client, monkeypatch):
    from app.services import image_service as svc

    async def _boom(*a, **k):
        raise RuntimeError("FAL down")

    monkeypatch.setattr(svc.fal_client, "subscribe_async", _boom, raising=False)
    out = await client.generate("test prompt")
    assert out is None


# AC-IMG-1: returns None when response lacks images
@pytest.mark.asyncio
async def test_generate_returns_none_on_empty_response(client, monkeypatch):
    from app.services import image_service as svc

    monkeypatch.setattr(svc.fal_client, "subscribe_async",
                        AsyncMock(return_value={"images": []}), raising=False)
    out = await client.generate("p")
    assert out is None


# AC-IMG-1: returns None on timeout
@pytest.mark.asyncio
async def test_generate_returns_none_on_timeout(client, monkeypatch):
    import asyncio
    from app.services import image_service as svc

    async def _hang(*a, **k):
        await asyncio.sleep(5)
        return {}

    monkeypatch.setattr(svc.fal_client, "subscribe_async", _hang, raising=False)
    out = await client.generate("p", timeout_s=0.05)
    assert out is None


# AC-IMG-1: happy path
@pytest.mark.asyncio
async def test_generate_returns_url_on_success(client, monkeypatch):
    from app.services import image_service as svc

    monkeypatch.setattr(
        svc.fal_client, "subscribe_async",
        AsyncMock(return_value={"images": [{"url": "https://fal.media/x.jpg"}]}),
        raising=False,
    )
    out = await client.generate("p")
    assert out == "https://fal.media/x.jpg"


# AC-IMG-2: disabled short-circuits
@pytest.mark.asyncio
async def test_generate_short_circuits_when_disabled(client, monkeypatch):
    from app.services import image_service as svc

    called = AsyncMock(return_value={"images": [{"url": "x"}]})
    monkeypatch.setattr(svc.fal_client, "subscribe_async", called, raising=False)
    monkeypatch.setattr(svc, "_image_gen_enabled", lambda: False, raising=False)

    out = await client.generate("p")
    assert out is None
    called.assert_not_called()


# AC-IMG-NSFW-1: FAL safety filter -> black-square redaction must not leak
@pytest.mark.asyncio
async def test_generate_returns_none_when_nsfw_flag_is_true(client, monkeypatch):
    from app.services import image_service as svc

    monkeypatch.setattr(
        svc.fal_client,
        "subscribe_async",
        AsyncMock(
            return_value={
                "images": [{"url": "https://fal.media/files/x/black.jpg"}],
                "has_nsfw_concepts": [True],
                "seed": 42,
            }
        ),
        raising=False,
    )
    out = await client.generate("p")
    assert out is None


# AC-IMG-NSFW-1: list with False (no NSFW) -> URL still returned
@pytest.mark.asyncio
async def test_generate_returns_url_when_nsfw_flag_is_false(client, monkeypatch):
    from app.services import image_service as svc

    monkeypatch.setattr(
        svc.fal_client,
        "subscribe_async",
        AsyncMock(
            return_value={
                "images": [{"url": "https://fal.media/files/x/ok.jpg"}],
                "has_nsfw_concepts": [False],
            }
        ),
        raising=False,
    )
    out = await client.generate("p")
    assert out == "https://fal.media/files/x/ok.jpg"


# AC-IMG-NSFW-1: missing key -> backwards-compatible (URL returned)
@pytest.mark.asyncio
async def test_generate_returns_url_when_nsfw_flag_absent(client, monkeypatch):
    from app.services import image_service as svc

    monkeypatch.setattr(
        svc.fal_client,
        "subscribe_async",
        AsyncMock(return_value={"images": [{"url": "https://fal.media/x.jpg"}]}),
        raising=False,
    )
    out = await client.generate("p")
    assert out == "https://fal.media/x.jpg"
