"""Minimal OAuth 1.0a (HMAC-SHA1) request signing for the X API. Stdlib-only.

X API v2 write endpoints accept OAuth 1.0a user context. For JSON-body
requests, only the oauth_* parameters and URL query parameters participate in
the signature base string (RFC 5849 §3.4.1.3 — JSON bodies are not form-encoded
and are therefore excluded).

Verified against the canonical example from the X developer docs
("Creating a signature") in tests/test_oauth1.py.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
import urllib.parse


def pct(value: str) -> str:
    """RFC 3986 percent-encoding as OAuth 1.0a requires (encode everything
    except unreserved characters)."""
    return urllib.parse.quote(str(value), safe="~-._")


def signature_base_string(method: str, base_url: str, params: dict[str, str]) -> str:
    pairs = sorted((pct(k), pct(v)) for k, v in params.items())
    param_str = "&".join(f"{k}={v}" for k, v in pairs)
    return "&".join([method.upper(), pct(base_url), pct(param_str)])


def sign(base_string: str, consumer_secret: str, token_secret: str) -> str:
    key = f"{pct(consumer_secret)}&{pct(token_secret)}".encode()
    digest = hmac.new(key, base_string.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def authorization_header(
    method: str,
    url: str,
    *,
    consumer_key: str,
    consumer_secret: str,
    token: str,
    token_secret: str,
    query_params: dict[str, str] | None = None,
    form_params: dict[str, str] | None = None,
    nonce: str | None = None,
    timestamp: str | None = None,
) -> str:
    """Build the ``Authorization: OAuth ...`` header value for a request.

    ``url`` must be the base URL WITHOUT query string; pass query params
    separately. ``form_params`` only for application/x-www-form-urlencoded
    bodies (JSON bodies: leave None).
    """
    oauth_params = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": nonce or secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": timestamp or str(int(time.time())),
        "oauth_token": token,
        "oauth_version": "1.0",
    }
    all_params: dict[str, str] = {}
    all_params.update(query_params or {})
    all_params.update(form_params or {})
    all_params.update(oauth_params)

    base = signature_base_string(method, url, all_params)
    oauth_params["oauth_signature"] = sign(base, consumer_secret, token_secret)

    header_parts = ", ".join(
        f'{pct(k)}="{pct(v)}"' for k, v in sorted(oauth_params.items())
    )
    return f"OAuth {header_parts}"
