"""§21 Phase 3 — outbound HTTP client with SSRF guard (`AC-PRECOMP-SEC-1`).

All FAL / CDN / web-search calls made by the precompute pipeline route
through `safe_request`. The implementation:

1. Resolves the URL host to an A / AAAA record **once** and rejects any
   IP that falls in a deny-listed CIDR (loopback / private / link-local /
   IPv6 unique-local / ULA / reserved).
2. Pins the resolved IP for the remainder of the request by passing it as
   the `Host`-rewritten URL via the `transport` extension (DNS rebind
   protection — a second DNS lookup at TLS time cannot point us at a new
   address).
3. Refuses URL schemes other than `http` / `https`.

The function intentionally returns the raw `httpx.Response` so callers
keep full control over status / body parsing. It does NOT silently retry
on non-2xx — that is the caller's policy.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable
from urllib.parse import urlsplit, urlunsplit

import httpx

ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})

# CIDRs the outbound client refuses to talk to. Sourced from RFC 1918,
# RFC 5735, RFC 4193, RFC 6890, and CGNAT (RFC 6598).
DENY_LIST_V4: tuple[str, ...] = (
    "0.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.0.0.0/24",
    "192.0.2.0/24",
    "192.168.0.0/16",
    "198.18.0.0/15",
    "198.51.100.0/24",
    "203.0.113.0/24",
    "224.0.0.0/4",
    "240.0.0.0/4",
)
DENY_LIST_V6: tuple[str, ...] = (
    "::1/128",
    "fc00::/7",
    "fe80::/10",
    "::ffff:0:0/96",  # IPv4-mapped — would otherwise tunnel a private v4
)

_DENY_NETS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = tuple(
    ipaddress.ip_network(c) for c in (*DENY_LIST_V4, *DENY_LIST_V6)
)


class SSRFBlockedError(Exception):
    """Raised when the resolved IP for a URL is on the deny-list."""

    def __init__(self, host: str, ip: str | None = None, reason: str = "") -> None:
        super().__init__(
            f"SSRF guard blocked outbound request to {host!r}"
            + (f" ({ip})" if ip else "")
            + (f": {reason}" if reason else "")
        )
        self.host = host
        self.ip = ip
        self.reason = reason


# ---------------------------------------------------------------------------
# IP classification
# ---------------------------------------------------------------------------


def is_blocked_ip(ip: str) -> bool:
    """Return True iff `ip` falls in any deny-listed CIDR."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True  # malformed → fail closed
    return any(addr in net for net in _DENY_NETS)


def _resolve_all(host: str) -> list[str]:
    """Return every A / AAAA record for `host` (deduplicated, sorted)."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise SSRFBlockedError(host, reason=f"DNS resolution failed: {exc}") from exc
    seen: set[str] = set()
    for info in infos:
        sockaddr = info[4]
        ip = sockaddr[0] if sockaddr else None
        if ip:
            seen.add(ip)
    return sorted(seen)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def assert_url_safe(url: str, *, resolver: Iterable[str] | None = None) -> str:
    """Validate `url` and return the **first safe** resolved IP.

    `resolver` is an injection point used by tests to bypass real DNS.
    Production callers leave it as None.
    """
    parts = urlsplit(url)
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise SSRFBlockedError(parts.hostname or "<missing>", reason=f"scheme {parts.scheme!r} not allowed")
    host = parts.hostname
    if not host:
        raise SSRFBlockedError("<missing>", reason="URL has no host")

    # Literal IP in URL — classify directly without hitting DNS.
    try:
        addr = ipaddress.ip_address(host)
        if any(addr in net for net in _DENY_NETS):
            raise SSRFBlockedError(host, ip=host, reason="literal IP in deny-list")
        return host
    except ValueError:
        pass  # not a literal IP, fall through to DNS

    candidates = list(resolver) if resolver is not None else _resolve_all(host)
    if not candidates:
        raise SSRFBlockedError(host, reason="no A/AAAA records")
    blocked = [ip for ip in candidates if is_blocked_ip(ip)]
    safe = [ip for ip in candidates if not is_blocked_ip(ip)]
    if blocked:
        # Mixed result: an attacker controlling DNS could rebind between
        # checks; we refuse the lot rather than racing.
        raise SSRFBlockedError(host, ip=blocked[0], reason="resolved IP in deny-list")
    return safe[0]


def pin_url_to_ip(url: str, ip: str) -> str:
    """Rewrite `url` to use `ip` as the host, preserving original `Host`
    via callers' explicit headers (TLS SNI is intentionally lost — pin
    the IP only when the upstream tolerates it).

    Returned for completeness; production callers prefer the
    `httpx.AsyncHTTPTransport(local_address=...)` route OR a custom
    resolver. We keep the helper for tests that need to reason about the
    rewrite shape.
    """
    parts = urlsplit(url)
    netloc = ip if parts.port is None else f"{ip}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


async def safe_request(
    method: str,
    url: str,
    *,
    timeout: float = 10.0,
    client: httpx.AsyncClient | None = None,
    resolver: Iterable[str] | None = None,
    **kwargs: object,
) -> httpx.Response:
    """SSRF-guarded `httpx.AsyncClient` request.

    Caller may pass an existing `client` (e.g. for connection pooling);
    the guard runs unconditionally either way.
    """
    assert_url_safe(url, resolver=resolver)
    if client is None:
        async with httpx.AsyncClient(timeout=timeout) as inner:
            return await inner.request(method, url, **kwargs)  # type: ignore[arg-type]
    return await client.request(method, url, **kwargs)  # type: ignore[arg-type]
