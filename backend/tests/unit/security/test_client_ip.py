"""Unit tests for `_client_ip` in app.security.rate_limit.

The handler trusts the first hop in `X-Forwarded-For` (set by Kong), so
incorrect parsing here would break per-IP rate limiting in production.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.security.rate_limit import _client_ip


def _req(*, headers: dict[str, str] | None = None, host: str | None = "1.2.3.4"):
    h = headers or {}
    # Starlette's Request.headers is case-insensitive; emulate via lowercase get.
    fake_headers = {k.lower(): v for k, v in h.items()}
    return SimpleNamespace(
        headers=SimpleNamespace(get=lambda k, default=None: fake_headers.get(k.lower(), default)),
        client=SimpleNamespace(host=host) if host is not None else None,
    )


class TestClientIp:
    def test_xff_first_hop_used(self):
        r = _req(headers={"X-Forwarded-For": "9.9.9.9, 1.1.1.1, 8.8.8.8"})
        assert _client_ip(r) == "9.9.9.9"

    def test_xff_single_value(self):
        r = _req(headers={"X-Forwarded-For": "9.9.9.9"})
        assert _client_ip(r) == "9.9.9.9"

    def test_xff_whitespace_trimmed(self):
        r = _req(headers={"X-Forwarded-For": "  9.9.9.9  ,  1.1.1.1"})
        assert _client_ip(r) == "9.9.9.9"

    def test_empty_xff_falls_back_to_request_client(self):
        r = _req(headers={"X-Forwarded-For": ""}, host="5.6.7.8")
        assert _client_ip(r) == "5.6.7.8"

    def test_no_xff_uses_request_client(self):
        r = _req(host="5.6.7.8")
        assert _client_ip(r) == "5.6.7.8"

    def test_no_client_returns_unknown(self):
        r = _req(host=None)
        assert _client_ip(r) == "unknown"

    def test_xff_first_hop_blank_returns_unknown(self):
        # First hop is blank ("") after split → should yield "unknown".
        r = _req(headers={"X-Forwarded-For": " ,1.1.1.1"})
        assert _client_ip(r) == "unknown"

    def test_request_client_attribute_error_returns_unknown(self):
        # Force AttributeError by providing a client object that raises on .host.
        class _Bad:
            @property
            def host(self):
                raise RuntimeError("nope")

        r = SimpleNamespace(
            headers=SimpleNamespace(get=lambda k, default=None: None),
            client=_Bad(),
        )
        assert _client_ip(r) == "unknown"
