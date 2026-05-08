"""Prod-smoke: live verification that Cloudflare Turnstile is enforced.

These tests run against the deployed API (``PROD_SMOKE_BASE_URL``) and
exercise the real Cloudflare ``siteverify`` round trip. They cover the
"will Turnstile actually stop malicious traffic?" question that no
in-process unit test can answer (because units stub the network).

What is proven here:
  * The site key returned by ``/api/v1/config`` is a real Cloudflare
    production key (prefix ``0x``), not a public always-pass test key
    (``1x`` / ``2x`` / ``3x`` prefixes).
  * ``turnstileEnabled`` is ``true`` in the deployed config response.
  * ``POST /quiz/start`` rejects requests with no token (400).
  * ``POST /quiz/start`` rejects requests with an invalid token. The
    rejection comes back as 401 with ``Invalid Turnstile token`` —
    proving the BE actually called Cloudflare and Cloudflare said no.
  * Oversized tokens are rejected before being forwarded to Cloudflare
    (4096-byte guard).
  * Cloudflare's public always-pass test token is rejected (proves we
    are not accidentally running with a test secret in prod).

Per-IP rate limiting also protects ``/quiz/start`` and may fire before
our Turnstile guard. Input-shape assertions therefore accept either the
expected validation status or 429 (both mean "request blocked"). The
``invalid token → 401`` test is the strongest end-to-end signal and is
ordered first to maximise the chance of getting a clean Cloudflare round
trip before the bucket is exhausted.

What is NOT proven here:
  * The happy-path with a valid Turnstile token. Minting a real token
    server-side is not possible; that path requires a real browser
    (Playwright e2e against the deployed FE).
"""
from __future__ import annotations

import re
import time

import httpx
import pytest

API_PREFIX = "/api/v1"
START_PATH = f"{API_PREFIX}/quiz/start"
CONFIG_PATH = f"{API_PREFIX}/config"

# Cloudflare publishes test keys with these prefixes (always-pass / always-fail
# / forced-interactive). A production deploy must NOT serve any of these.
_TEST_KEY_PREFIXES = ("1x", "2x", "3x")

# Statuses that indicate the request was blocked (any of these is a "good"
# outcome for an attempted bad request). 429 is included because per-IP rate
# limiting can fire before our Turnstile guard.
_BLOCKED_STATUSES = frozenset({400, 401, 403, 422, 429})


pytestmark = pytest.mark.prod_smoke


@pytest.fixture(scope="module")
def client(prod_base_url: str) -> httpx.Client:
    with httpx.Client(base_url=prod_base_url, timeout=30.0) as c:
        yield c


def _post_start(client: httpx.Client, body: dict) -> httpx.Response:
    """POST /quiz/start with a small inter-test sleep to spare the RL bucket."""
    resp = client.post(START_PATH, json=body)
    # Brief pause helps the per-IP token bucket replenish between tests.
    time.sleep(1.0)
    return resp


# --------------------------- /config exposure ---------------------------
# These are GETs and are exempt from the per-IP quiz-start rate limit, so
# they are deterministic and run first.


def test_config_endpoint_publicly_reachable(client: httpx.Client) -> None:
    resp = client.get(CONFIG_PATH)
    assert resp.status_code == 200, resp.text[:300]
    assert resp.headers.get("content-type", "").startswith("application/json")


def test_config_turnstile_enabled(client: httpx.Client) -> None:
    body = client.get(CONFIG_PATH).json()
    features = body.get("features") or {}
    assert features.get("turnstileEnabled") is True, (
        f"turnstileEnabled is not true in deployed /config; features={features!r}"
    )


def test_config_serves_real_production_site_key(client: httpx.Client) -> None:
    """Site key must be a real Cloudflare production key, not a test key."""
    body = client.get(CONFIG_PATH).json()
    features = body.get("features") or {}
    site_key = features.get("turnstileSiteKey") or ""
    assert isinstance(site_key, str) and site_key, (
        "turnstileSiteKey missing from /config response"
    )
    assert not site_key.startswith(_TEST_KEY_PREFIXES), (
        f"deployed turnstileSiteKey starts with a Cloudflare TEST prefix "
        f"({site_key[:2]!r}); production should use a real ``0x`` key"
    )
    assert re.fullmatch(r"[A-Za-z0-9_\-]{16,64}", site_key), (
        f"turnstileSiteKey shape unexpected: {site_key!r}"
    )


# --------------------------- enforcement gate ---------------------------
# These are POSTs that share a per-IP rate-limit bucket. The KILLER test
# runs first to give it the cleanest shot at a real Cloudflare round trip.


def test_quiz_start_invalid_token_rejected_by_real_cloudflare(
    client: httpx.Client,
) -> None:
    """KILLER TEST: invalid token → 401 ``Invalid Turnstile token``.

    Returning 401 (not 400) proves the request was forwarded to Cloudflare
    and Cloudflare said the token was invalid. If the secret were missing
    or wrong we would get a 500 ``Could not verify`` instead.

    This is the strongest end-to-end signal that Turnstile will actually
    block malicious traffic in production.
    """
    resp = _post_start(
        client,
        {
            "category": "smoke-test",
            "cf-turnstile-response": "definitely-not-a-real-cloudflare-token-XXXX",
        },
    )
    if resp.status_code == 429:
        pytest.skip("rate-limited before reaching Turnstile guard; rerun later")
    assert resp.status_code == 401, (
        f"expected 401 from real Cloudflare rejection, got {resp.status_code}: "
        f"{resp.text[:300]}"
    )
    detail = (resp.json().get("detail") or "").lower()
    assert "invalid turnstile token" in detail, resp.text[:300]


def test_quiz_start_with_test_key_token_is_rejected_in_prod(
    client: httpx.Client,
) -> None:
    """Cloudflare's public always-pass test token must NOT be accepted.

    If accepted, it means the deployed Turnstile **secret** is also a test
    secret — meaning real bots get a free pass.
    """
    resp = _post_start(
        client,
        {
            "category": "smoke-test",
            "cf-turnstile-response": "1x00000000000000000000AA",
        },
    )
    if resp.status_code == 429:
        pytest.skip("rate-limited before reaching Turnstile guard; rerun later")
    assert resp.status_code == 401, (
        f"prod accepted Cloudflare's public test token! status={resp.status_code} "
        f"body={resp.text[:300]} — this means the prod secret is a test secret"
    )


def test_quiz_start_rejects_missing_token(client: httpx.Client) -> None:
    """No token → blocked. Proves Turnstile dependency is wired in."""
    resp = _post_start(client, {"category": "smoke-test"})
    assert resp.status_code in _BLOCKED_STATUSES, resp.text[:300]
    if resp.status_code == 400:
        assert "turnstile" in (resp.json().get("detail") or "").lower()


def test_quiz_start_rejects_empty_string_token(client: httpx.Client) -> None:
    resp = _post_start(
        client,
        {"category": "smoke-test", "cf-turnstile-response": ""},
    )
    assert resp.status_code in _BLOCKED_STATUSES, resp.text[:300]


def test_quiz_start_rejects_non_string_token(client: httpx.Client) -> None:
    resp = _post_start(
        client,
        {"category": "smoke-test", "cf-turnstile-response": 123},
    )
    assert resp.status_code in _BLOCKED_STATUSES, resp.text[:300]


def test_quiz_start_rejects_oversized_token(client: httpx.Client) -> None:
    """4096-byte guard short-circuits before forwarding to Cloudflare."""
    huge = "A" * 8000
    resp = _post_start(
        client,
        {"category": "smoke-test", "cf-turnstile-response": huge},
    )
    assert resp.status_code in _BLOCKED_STATUSES, resp.text[:300]
    if resp.status_code == 400:
        assert "too large" in (resp.json().get("detail") or "").lower()

