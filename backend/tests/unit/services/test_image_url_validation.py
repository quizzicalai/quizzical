"""§9.7.1 — Image URL validation (AC-IMG-URL-1..7).

The URL returned by FAL is treated as untrusted input. Only ``https://``
URLs whose host is in the configured allowlist are accepted; everything
else is rejected (returned as ``None``) so that no malformed or
malicious URL can ever reach the DB or the frontend.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.core.config import settings
from app.services import image_service as imgsvc
from app.services import retry as retry_mod


# ---------------------------------------------------------------------------
# Direct unit tests on the validator
# ---------------------------------------------------------------------------

class TestValidateImageUrl:
    def test_https_allowed_host_passes(self):
        url = "https://v3.fal.media/files/abc.png"
        assert imgsvc._validate_image_url(url) == url

    def test_https_subdomain_of_allowed_host_passes(self):
        url = "https://sub.fal.media/x.png"
        assert imgsvc._validate_image_url(url) == url

    def test_javascript_scheme_rejected(self):
        assert imgsvc._validate_image_url("javascript:alert(1)") is None

    def test_data_scheme_rejected(self):
        assert imgsvc._validate_image_url("data:image/png;base64,xxx") is None

    def test_http_rejected(self):
        assert imgsvc._validate_image_url("http://fal.media/x.png") is None

    def test_disallowed_host_rejected(self):
        assert imgsvc._validate_image_url("https://evil.example.com/x.png") is None

    def test_empty_or_none_rejected(self):
        assert imgsvc._validate_image_url("") is None
        assert imgsvc._validate_image_url(None) is None  # type: ignore[arg-type]

    def test_malformed_url_rejected(self):
        assert imgsvc._validate_image_url("not a url") is None
        assert imgsvc._validate_image_url("https://") is None

    def test_empty_allowlist_disables_host_check(self, monkeypatch):
        # AC-IMG-URL-7
        monkeypatch.setattr(settings.image_gen, "url_allowlist", [], raising=False)
        assert imgsvc._validate_image_url("https://anything.com/x.png") == \
            "https://anything.com/x.png"
        assert imgsvc._validate_image_url("javascript:bad") is None


# ---------------------------------------------------------------------------
# Integration with FalImageClient.generate()
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _sleep(_s: float) -> None:
        return None
    monkeypatch.setattr(retry_mod.asyncio, "sleep", _sleep)


@pytest.fixture(autouse=True)
def _enable_fal(monkeypatch):
    cfg = getattr(settings, "image_gen", None)
    monkeypatch.setattr(cfg, "enabled", True)
    monkeypatch.setenv("FAL_KEY", "test-key")
    monkeypatch.setattr(imgsvc, "_image_gen_enabled", lambda: True, raising=False)


@pytest.fixture
def fal_resp(monkeypatch):
    state: dict[str, Any] = {"resp": None}

    async def _fake_subscribe(model: str, *, arguments: dict):
        return state["resp"]

    monkeypatch.setattr(imgsvc.fal_client, "subscribe_async", _fake_subscribe)
    return state


@pytest.mark.asyncio
class TestGenerateAppliesValidation:
    async def test_generate_returns_none_for_javascript_url(self, fal_resp):
        fal_resp["resp"] = {"images": [{"url": "javascript:alert(1)"}]}
        client = imgsvc.FalImageClient()
        assert await client.generate("a portrait") is None

    async def test_generate_returns_none_for_disallowed_host(self, fal_resp):
        fal_resp["resp"] = {"images": [{"url": "https://evil.example.com/x.png"}]}
        client = imgsvc.FalImageClient()
        assert await client.generate("a portrait") is None

    async def test_generate_passes_through_valid_https_url(self, fal_resp):
        good = "https://v3.fal.media/files/good.png"
        fal_resp["resp"] = {"images": [{"url": good}]}
        client = imgsvc.FalImageClient()
        assert await client.generate("a portrait") == good
