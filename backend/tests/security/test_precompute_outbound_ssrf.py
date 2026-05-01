"""§21 Phase 3 — SSRF guard tests (`AC-PRECOMP-SEC-1`).

These tests deliberately bypass real DNS by passing the `resolver=`
override so the suite stays hermetic and never accidentally talks to a
live endpoint.
"""

from __future__ import annotations

import pytest

from app.services.precompute.outbound import (
    SSRFBlockedError,
    assert_url_safe,
    is_blocked_ip,
    pin_url_to_ip,
)


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1", "10.0.0.1", "192.168.1.1", "169.254.169.254",
        "172.16.0.1", "100.64.0.1", "0.0.0.0",
        "::1", "fc00::1", "fe80::1",
        "::ffff:10.0.0.1",  # IPv4-mapped private
        "not-an-ip",        # malformed → fail closed
    ],
)
def test_blocked_ips_classified_correctly(ip: str) -> None:
    assert is_blocked_ip(ip) is True


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1", "2001:4860:4860::8888"])
def test_public_ips_pass(ip: str) -> None:
    assert is_blocked_ip(ip) is False


def test_assert_url_safe_rejects_bad_scheme() -> None:
    with pytest.raises(SSRFBlockedError):
        assert_url_safe("file:///etc/passwd", resolver=["8.8.8.8"])
    with pytest.raises(SSRFBlockedError):
        assert_url_safe("gopher://example.test/x", resolver=["8.8.8.8"])


def test_assert_url_safe_rejects_literal_private_ip() -> None:
    with pytest.raises(SSRFBlockedError) as exc:
        assert_url_safe("http://127.0.0.1/admin", resolver=["8.8.8.8"])
    assert "deny-list" in str(exc.value)


def test_assert_url_safe_rejects_dns_resolving_to_private() -> None:
    with pytest.raises(SSRFBlockedError):
        assert_url_safe(
            "http://attacker.example.test/x",
            resolver=["10.0.0.1"],  # spoofed resolution
        )


def test_assert_url_safe_rejects_mixed_resolution() -> None:
    # Mixed public + private result is treated as a rebind attempt.
    with pytest.raises(SSRFBlockedError):
        assert_url_safe(
            "http://attacker.example.test/x",
            resolver=["8.8.8.8", "10.0.0.1"],
        )


def test_assert_url_safe_returns_safe_ip_for_public_host() -> None:
    ip = assert_url_safe(
        "https://api.example.test/v1", resolver=["1.1.1.1"],
    )
    assert ip == "1.1.1.1"


def test_pin_url_to_ip_preserves_path_and_port() -> None:
    out = pin_url_to_ip("https://api.example.test:8443/v1?x=1#f", "1.2.3.4")
    assert out == "https://1.2.3.4:8443/v1?x=1#f"
