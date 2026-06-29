"""Unit tests for `_client_ip` in app.security.rate_limit.

The resolver is trusted-proxy aware: Azure Container Apps (and most
ingresses) APPEND the connecting peer to ``X-Forwarded-For``, so the genuine
client is the Nth hop from the RIGHT where N == TRUSTED_PROXY_HOPS (default
1). Trusting the left-most hop (the old behaviour) let a client rotate the
header to mint a fresh rate-limit bucket per request — verified spoofable
live on 2026-06-28.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.security.rate_limit import _client_ip


def _req(*, headers: dict[str, str] | None = None, host: str | None = "1.2.3.4"):
    h = headers or {}
    fake_headers = {k.lower(): v for k, v in h.items()}
    return SimpleNamespace(
        headers=SimpleNamespace(get=lambda k, default=None: fake_headers.get(k.lower(), default)),
        client=SimpleNamespace(host=host) if host is not None else None,
    )


class TestClientIp:
    def test_trusted_hop_is_rightmost_not_spoofable_left(self):
        # Left hops are attacker-controlled; the real client is the one the
        # trusted proxy appended on the right.
        r = _req(headers={"X-Forwarded-For": "9.9.9.9, 1.1.1.1, 8.8.8.8"})
        assert _client_ip(r) == "8.8.8.8"

    def test_xff_single_value(self):
        r = _req(headers={"X-Forwarded-For": "9.9.9.9"})
        assert _client_ip(r) == "9.9.9.9"

    def test_spoofed_left_hop_ignored(self):
        # The classic spoof: attacker prepends a fake IP, proxy appends real one.
        r = _req(headers={"X-Forwarded-For": "203.0.113.250, 5.6.7.8"}, host="9.9.9.9")
        assert _client_ip(r) == "5.6.7.8"

    def test_whitespace_trimmed_rightmost(self):
        r = _req(headers={"X-Forwarded-For": "  9.9.9.9  ,  1.1.1.1 "})
        assert _client_ip(r) == "1.1.1.1"

    def test_empty_xff_falls_back_to_peer(self):
        r = _req(headers={"X-Forwarded-For": ""}, host="5.6.7.8")
        assert _client_ip(r) == "5.6.7.8"

    def test_no_xff_uses_peer(self):
        r = _req(host="5.6.7.8")
        assert _client_ip(r) == "5.6.7.8"

    def test_no_client_returns_unknown(self):
        r = _req(host=None)
        assert _client_ip(r) == "unknown"

    def test_non_ip_at_trusted_position_falls_back_to_peer(self):
        # A garbage/non-IP value at the trusted hop must NOT be used and must
        # NOT fall back to a spoofable left value — use the connecting peer.
        r = _req(headers={"X-Forwarded-For": "9.9.9.9, not-an-ip"}, host="5.6.7.8")
        assert _client_ip(r) == "5.6.7.8"

    def test_request_client_attribute_error_returns_unknown(self):
        class _Bad:
            @property
            def host(self):
                raise RuntimeError("nope")

        r = SimpleNamespace(
            headers=SimpleNamespace(get=lambda k, default=None: None),
            client=_Bad(),
        )
        assert _client_ip(r) == "unknown"

    def test_trusted_proxy_hops_env_override(self, monkeypatch):
        # With 2 trusted proxies, the real client is the 2nd from the right.
        monkeypatch.setenv("TRUSTED_PROXY_HOPS", "2")
        r = _req(headers={"X-Forwarded-For": "9.9.9.9, 5.6.7.8, 10.0.0.1"})
        assert _client_ip(r) == "5.6.7.8"

    def test_misconfigured_hops_too_many_falls_back_to_peer(self, monkeypatch):
        monkeypatch.setenv("TRUSTED_PROXY_HOPS", "5")
        r = _req(headers={"X-Forwarded-For": "9.9.9.9, 5.6.7.8"}, host="1.2.3.4")
        assert _client_ip(r) == "1.2.3.4"
