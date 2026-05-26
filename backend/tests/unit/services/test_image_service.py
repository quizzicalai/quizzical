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


# --- AC-IMG-REQSHAPE-1 ------------------------------------------------------
# These tests pin the EXACT request body sent to FAL. Image quality and cost
# both depend on this dict literally, so any change here is a wire-format
# breaking change that must be opted into explicitly.
@pytest.mark.asyncio
async def test_generate_sends_required_fal_request_shape(client, monkeypatch):
    """Every FAL call MUST include prompt + image_size + steps + safety
    checker. Missing any of these silently changes generation behaviour
    (e.g. dropping enable_safety_checker disables NSFW filtering)."""
    from app.services import image_service as svc

    spy = AsyncMock(return_value={"images": [{"url": "https://fal.media/x.jpg"}]})
    monkeypatch.setattr(svc.fal_client, "subscribe_async", spy, raising=False)

    await client.generate("hello world")

    spy.assert_awaited_once()
    args, kwargs = spy.call_args
    # First positional arg is the model id; must be a non-empty string.
    assert isinstance(args[0], str) and args[0]
    # All FAL parameters MUST be passed via the `arguments=` kwarg.
    body = kwargs["arguments"]
    assert body["prompt"] == "hello world"
    assert isinstance(body["image_size"], dict)
    assert {"width", "height"} <= body["image_size"].keys()
    assert isinstance(body["num_inference_steps"], int)
    assert body["num_inference_steps"] >= 1
    # Critical safety guardrail — must never be silently disabled.
    assert body["enable_safety_checker"] is True


@pytest.mark.asyncio
async def test_generate_forwards_negative_prompt_and_seed_when_provided(
    client, monkeypatch
):
    """Determinism contract (AC-IMG-STYLE-4) + style consistency: when the
    caller passes a seed and negative prompt, both MUST be forwarded
    verbatim. The seed must also be masked into uint32 range to satisfy
    FAL's API."""
    from app.services import image_service as svc

    spy = AsyncMock(return_value={"images": [{"url": "https://fal.media/x.jpg"}]})
    monkeypatch.setattr(svc.fal_client, "subscribe_async", spy, raising=False)

    # Pick a value that exercises the uint32 mask: anything > 2**32.
    raw_seed = (1 << 33) | 0xABCD
    await client.generate(
        "p", negative_prompt="text, watermark", seed=raw_seed
    )

    body = spy.call_args.kwargs["arguments"]
    assert body["negative_prompt"] == "text, watermark"
    assert body["seed"] == raw_seed & 0xFFFFFFFF
    assert 0 <= body["seed"] <= 0xFFFFFFFF


@pytest.mark.asyncio
async def test_generate_omits_optional_fields_when_not_provided(
    client, monkeypatch
):
    """negative_prompt and seed are OPT-IN. Sending them as None / empty
    must NOT include the keys (FAL treats explicit nulls differently
    from missing keys for `seed`, which would defeat determinism)."""
    from app.services import image_service as svc

    spy = AsyncMock(return_value={"images": [{"url": "https://fal.media/x.jpg"}]})
    monkeypatch.setattr(svc.fal_client, "subscribe_async", spy, raising=False)

    await client.generate("p")

    body = spy.call_args.kwargs["arguments"]
    assert "negative_prompt" not in body
    assert "seed" not in body
