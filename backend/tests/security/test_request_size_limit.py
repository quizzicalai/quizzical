"""Reject oversized request bodies with HTTP 413."""
from __future__ import annotations

import os

import pytest


@pytest.mark.asyncio
async def test_oversized_content_length_rejected(async_client) -> None:
    # Default cap is 256 KiB; advertise 1 MiB to trip the limit.
    headers = {"content-type": "application/json", "content-length": str(1024 * 1024)}
    r = await async_client.post("/api/quiz/start", content=b"{}", headers=headers)
    assert r.status_code == 413, r.text
    assert r.json()["errorCode"] == "PAYLOAD_TOO_LARGE"


@pytest.mark.asyncio
async def test_invalid_content_length_rejected(async_client) -> None:
    headers = {"content-type": "application/json", "content-length": "not-a-number"}
    r = await async_client.post("/api/quiz/start", content=b"{}", headers=headers)
    assert r.status_code == 400
    assert r.json()["errorCode"] == "BAD_REQUEST"


@pytest.mark.asyncio
async def test_get_requests_skip_size_check(async_client) -> None:
    # GETs never have meaningful bodies; we should not 413 on them.
    headers = {"content-length": str(10 * 1024 * 1024)}
    r = await async_client.get("/health", headers=headers)
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_env_override_raises_limit(async_client, monkeypatch) -> None:
    # Bump the cap to 2 MiB; a 1 MiB CL should now be allowed past the
    # middleware (it'll fail downstream for other reasons but not 413).
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", str(2 * 1024 * 1024))
    headers = {"content-type": "application/json", "content-length": str(1024 * 1024)}
    r = await async_client.post("/api/quiz/start", content=b"{}", headers=headers)
    assert r.status_code != 413, r.text


def test_default_limit_constant() -> None:
    from app.main import _max_body_bytes

    # Bad value falls back to 256 KiB.
    os.environ.pop("MAX_REQUEST_BODY_BYTES", None)
    assert _max_body_bytes() == 256 * 1024
