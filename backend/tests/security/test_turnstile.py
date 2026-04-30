"""
Iteration 1 — Security: Turnstile + secret hygiene.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import HTTPException

from app.api import dependencies as deps


class _Req:
    def __init__(self, payload):
        self._payload = payload

    async def body(self) -> bytes:
        if self._payload is None:
            return b""
        if isinstance(self._payload, (bytes, bytearray)):
            return bytes(self._payload)
        return json.dumps(self._payload).encode("utf-8")


def _force_turnstile(monkeypatch, *, enabled: bool, env: str, secret: str | None):
    """Patch the underlying SecurityConfig fields the legacy properties read."""
    monkeypatch.setattr(deps.settings.security, "enabled", enabled, raising=False)
    monkeypatch.setattr(
        deps.settings.security.turnstile, "secret_key", secret, raising=False
    )
    monkeypatch.setattr(deps.settings.app, "environment", env, raising=False)


# ---------------------------------------------------------------------------
# Bypass paths
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_verify_turnstile_bypass_when_disabled(monkeypatch):
    _force_turnstile(monkeypatch, enabled=False, env="production", secret="real-secret")
    assert await deps.verify_turnstile(_Req({"cf-turnstile-response": "x"})) is True


@pytest.mark.asyncio
async def test_verify_turnstile_local_bypass_with_default_secret(monkeypatch):
    _force_turnstile(
        monkeypatch, enabled=True, env="local", secret="your_turnstile_secret_key"
    )
    assert await deps.verify_turnstile(_Req({"cf-turnstile-response": "any"})) is True


# ---------------------------------------------------------------------------
# Failure paths
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_verify_turnstile_missing_token_raises_400(monkeypatch):
    _force_turnstile(monkeypatch, enabled=True, env="production", secret="real-secret")
    with pytest.raises(HTTPException) as exc:
        await deps.verify_turnstile(_Req({}))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_verify_turnstile_failed_check_raises_401(monkeypatch):
    _force_turnstile(monkeypatch, enabled=True, env="production", secret="real-secret")

    fake_resp = MagicMock()
    fake_resp.raise_for_status = MagicMock(return_value=None)
    fake_resp.json = MagicMock(return_value={"success": False, "error-codes": ["bad"]})

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    fake_client.post = AsyncMock(return_value=fake_resp)

    with patch.object(httpx, "AsyncClient", return_value=fake_client):
        with pytest.raises(HTTPException) as exc:
            await deps.verify_turnstile(_Req({"cf-turnstile-response": "tok"}))

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_verify_turnstile_network_error_returns_500(monkeypatch):
    _force_turnstile(monkeypatch, enabled=True, env="production", secret="real-secret")

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    fake_client.post = AsyncMock(side_effect=httpx.ConnectError("dns down"))

    with patch.object(httpx, "AsyncClient", return_value=fake_client):
        with pytest.raises(HTTPException) as exc:
            await deps.verify_turnstile(_Req({"cf-turnstile-response": "tok"}))

    assert exc.value.status_code == 500
    assert "real-secret" not in (exc.value.detail or "")


@pytest.mark.asyncio
async def test_verify_turnstile_handles_non_json_body(monkeypatch):
    _force_turnstile(monkeypatch, enabled=True, env="production", secret="real-secret")
    with pytest.raises(HTTPException) as exc:
        await deps.verify_turnstile(_Req(b"garbage-bytes"))
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# Secret hygiene
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_verify_turnstile_sends_secret_to_cloudflare_only(monkeypatch):
    """Secret must travel to Cloudflare's HTTPS endpoint, not in URL or response."""
    _force_turnstile(
        monkeypatch, enabled=True, env="production", secret="super-secret-xyz"
    )

    captured = {}
    fake_resp = MagicMock()
    fake_resp.raise_for_status = MagicMock(return_value=None)
    fake_resp.json = MagicMock(return_value={"success": True})

    async def _post(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return fake_resp

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    fake_client.post = _post

    with patch.object(httpx, "AsyncClient", return_value=fake_client):
        ok = await deps.verify_turnstile(_Req({"cf-turnstile-response": "tok"}))

    assert ok is True
    assert captured["url"].startswith("https://challenges.cloudflare.com/turnstile/")
    body = captured["kwargs"].get("json") or captured["kwargs"].get("data") or {}
    assert "super-secret-xyz" in str(body)
    assert "super-secret-xyz" not in captured["url"]


# ---------------------------------------------------------------------------
# remoteip binding (Cloudflare best-practice — mitigates token replay across IPs)
# ---------------------------------------------------------------------------

class _ReqWithIP:
    """Minimal Request shim with headers + client.host for remoteip tests."""

    def __init__(self, payload, *, headers=None, client_host=None):
        self._payload = payload
        self.headers = headers or {}

        class _Client:
            def __init__(self, host):
                self.host = host

        self.client = _Client(client_host) if client_host else None

    async def body(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


async def _capture_siteverify_payload(monkeypatch, req) -> dict:
    """Run verify_turnstile against a fake httpx client and return the JSON payload sent."""
    captured: dict = {}
    fake_resp = MagicMock()
    fake_resp.raise_for_status = MagicMock(return_value=None)
    fake_resp.json = MagicMock(return_value={"success": True})

    async def _post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        return fake_resp

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=False)
    fake_client.post = _post

    with patch.object(httpx, "AsyncClient", return_value=fake_client):
        ok = await deps.verify_turnstile(req)

    assert ok is True
    return captured["json"] or {}


@pytest.mark.asyncio
async def test_verify_turnstile_passes_xff_first_hop_as_remoteip(monkeypatch):
    """Cloudflare best-practice: bind token to client IP via ``remoteip``."""
    _force_turnstile(
        monkeypatch, enabled=True, env="production", secret="real-secret"
    )
    req = _ReqWithIP(
        {"cf-turnstile-response": "tok"},
        headers={"x-forwarded-for": "203.0.113.7, 10.0.0.1"},
        client_host="10.0.0.99",
    )
    payload = await _capture_siteverify_payload(monkeypatch, req)
    assert payload.get("remoteip") == "203.0.113.7"


@pytest.mark.asyncio
async def test_verify_turnstile_falls_back_to_request_client_when_no_xff(monkeypatch):
    _force_turnstile(
        monkeypatch, enabled=True, env="production", secret="real-secret"
    )
    req = _ReqWithIP(
        {"cf-turnstile-response": "tok"},
        headers={},
        client_host="198.51.100.5",
    )
    payload = await _capture_siteverify_payload(monkeypatch, req)
    assert payload.get("remoteip") == "198.51.100.5"


@pytest.mark.asyncio
async def test_verify_turnstile_omits_remoteip_when_unknown(monkeypatch):
    """No headers, no client → omit ``remoteip`` rather than crash."""
    _force_turnstile(
        monkeypatch, enabled=True, env="production", secret="real-secret"
    )
    req = _ReqWithIP({"cf-turnstile-response": "tok"}, headers={}, client_host=None)
    payload = await _capture_siteverify_payload(monkeypatch, req)
    assert "remoteip" not in payload
    # Other required fields must still be present.
    assert payload.get("secret") == "real-secret"
    assert payload.get("response") == "tok"

