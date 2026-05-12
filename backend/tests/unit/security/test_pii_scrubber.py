# tests/unit/security/test_pii_scrubber.py
"""§15.5 — PII log scrubber (AC-LOGS-PII-1..4)."""
from __future__ import annotations

import pytest

from app.core.logging_config import _scrub_pii, _redact_in_mapping

pytestmark = [pytest.mark.unit]


# AC-LOGS-PII-1
def test_scrubs_email_address():
    out = _scrub_pii("Contact me at user@example.com please")
    assert "user@example.com" not in out
    assert "user@***" in out


# AC-LOGS-PII-2
def test_scrubs_credit_card_like():
    out = _scrub_pii("card 4111 1111 1111 1111 stored")
    assert "4111 1111 1111 1111" not in out
    assert "****1111" in out


# AC-LOGS-PII-3 — recursive over dict/list
def test_scrubs_nested_in_mapping():
    payload = {
        "msg": "user user@example.com",
        "items": ["card 4111111111111111", {"inner": "user2@x.org"}],
    }
    out = _redact_in_mapping(payload)
    assert "user@example.com" not in out["msg"]
    assert "user@***" in out["msg"]
    assert "4111111111111111" not in out["items"][0]
    assert "user2@x.org" not in out["items"][1]["inner"]


# AC-LOGS-PII-4 — idempotent
def test_idempotent():
    once = _scrub_pii("user@example.com")
    twice = _scrub_pii(once)
    assert once == twice


def test_scrubs_jwt_like_token():
    tok = "eyJhbGciOi.eyJzdWIi.signaturepart"
    out = _scrub_pii(f"auth={tok}")
    assert tok not in out
    assert "eyJ***" in out


def test_passthrough_clean_text():
    assert _scrub_pii("hello world 123") == "hello world 123"


# Regression: PAN regex must not falsely match digit runs embedded in
# hex/UUID strings (e.g., trace ids), which previously caused intermittent
# logging-middleware test failures depending on randomly generated UUIDs.
@pytest.mark.parametrize(
    "trace_id",
    [
        "11fcc891-bf8d-4faa-a185-63237388870d",  # the original failing UUID
        "00000000-0000-4000-8000-111111111111",
        "abcdef12-3456-4789-abcd-ef0123456789",
        "12345678-1234-1234-1234-123456789012",
    ],
)
def test_pan_regex_does_not_match_uuid(trace_id):
    assert _scrub_pii(trace_id) == trace_id, (
        f"UUID-like trace id was unexpectedly redacted: {_scrub_pii(trace_id)!r}"
    )
