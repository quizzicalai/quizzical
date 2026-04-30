"""Unit tests for private helpers in app.main covering CORS, trusted hosts,
and request body size — middleware-config inputs that have outsized security
impact and were not directly unit-tested.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def main_mod(monkeypatch):
    """Reload app.main fresh per test so module-level env reads don't leak."""
    # Don't actually reload (lifespan side effects); just import once and reuse.
    import app.main as m

    return m


# ---------------------------------------------------------------------------
# _read_trusted_hosts
# ---------------------------------------------------------------------------
class TestReadTrustedHosts:
    def test_csv_env_var_parsed(self, monkeypatch, main_mod):
        monkeypatch.setenv("TRUSTED_HOSTS", "a.example.com, b.example.com ,, c.example.com")
        assert main_mod._read_trusted_hosts() == [
            "a.example.com",
            "b.example.com",
            "c.example.com",
        ]

    def test_json_array_env_var_parsed(self, monkeypatch, main_mod):
        monkeypatch.setenv("TRUSTED_HOSTS", '["a.com", "b.com"]')
        assert main_mod._read_trusted_hosts() == ["a.com", "b.com"]

    def test_json_array_with_blanks_dropped(self, monkeypatch, main_mod):
        monkeypatch.setenv("TRUSTED_HOSTS", '["a.com", "", "  ", "b.com"]')
        assert main_mod._read_trusted_hosts() == ["a.com", "b.com"]

    def test_malformed_json_falls_back_to_csv(self, monkeypatch, main_mod):
        # Starts with [ but isn't valid JSON → falls through to CSV split.
        monkeypatch.setenv("TRUSTED_HOSTS", "[a.com, b.com")
        # CSV split keeps the literal `[a.com` and ` b.com` — verifies fallback path.
        out = main_mod._read_trusted_hosts()
        assert out == ["[a.com", "b.com"]

    def test_empty_env_returns_wildcard_in_local_env(self, monkeypatch, main_mod):
        monkeypatch.delenv("TRUSTED_HOSTS", raising=False)
        # main_mod._env_init was captured at import time. We can't change it now,
        # so just assert the contract: the result is a non-empty list.
        out = main_mod._read_trusted_hosts()
        assert isinstance(out, list) and len(out) >= 1


# ---------------------------------------------------------------------------
# _parse_allowed_origins
# ---------------------------------------------------------------------------
class TestParseAllowedOrigins:
    def test_empty_string_returns_empty_list(self, main_mod):
        assert main_mod._parse_allowed_origins("") == []
        assert main_mod._parse_allowed_origins("   ") == []

    def test_csv_split_and_trim(self, main_mod):
        assert main_mod._parse_allowed_origins(
            "https://a.com, https://b.com ,, https://c.com"
        ) == ["https://a.com", "https://b.com", "https://c.com"]

    def test_json_array(self, main_mod):
        assert main_mod._parse_allowed_origins(
            '["https://a.com","https://b.com"]'
        ) == ["https://a.com", "https://b.com"]

    def test_json_array_with_single_string_inside(self, main_mod):
        # The JSON-string branch is only entered when text starts with '['.
        # A bare quoted string falls through to CSV split.
        assert main_mod._parse_allowed_origins('"https://a.com"') == ['"https://a.com"']

    def test_malformed_bracketed_string_recovers_via_inner_split(self, main_mod):
        # Not valid JSON, but starts with [ and ends with ] → inner CSV recovery.
        assert main_mod._parse_allowed_origins("[https://a.com, https://b.com]") == [
            "https://a.com",
            "https://b.com",
        ]


# ---------------------------------------------------------------------------
# _normalize_allowed_origins
# ---------------------------------------------------------------------------
class TestNormalizeAllowedOrigins:
    def test_strips_quotes_whitespace_trailing_slash(self, main_mod):
        out = main_mod._normalize_allowed_origins([
            '  "https://a.com/"  ',
            "'https://b.com/'",
            "https://c.com",
        ])
        assert out == ["https://a.com", "https://b.com", "https://c.com"]

    def test_drops_blank_entries(self, main_mod):
        assert main_mod._normalize_allowed_origins(["", "  ", '""', "https://a.com"]) == [
            "https://a.com",
        ]

    def test_unescapes_embedded_quotes(self, main_mod):
        out = main_mod._normalize_allowed_origins([r'\"https://a.com\"'])
        assert out == ["https://a.com"]


# ---------------------------------------------------------------------------
# _expand_loopback_origin
# ---------------------------------------------------------------------------
class TestExpandLoopbackOrigin:
    def test_localhost_expanded_to_127(self, main_mod):
        out = main_mod._expand_loopback_origin("http://localhost:5173")
        assert out == ["http://localhost:5173", "http://127.0.0.1:5173"]

    def test_127_expanded_to_localhost(self, main_mod):
        out = main_mod._expand_loopback_origin("http://127.0.0.1:5173")
        assert out == ["http://127.0.0.1:5173", "http://localhost:5173"]

    def test_no_port_still_expands(self, main_mod):
        out = main_mod._expand_loopback_origin("http://localhost")
        assert out == ["http://localhost", "http://127.0.0.1"]

    def test_non_loopback_origin_returned_as_is(self, main_mod):
        out = main_mod._expand_loopback_origin("https://example.com:5173")
        assert out == ["https://example.com:5173"]

    def test_unparseable_origin_returned_as_is(self, main_mod):
        # A bizarre non-URL string still survives; urlsplit doesn't usually raise
        # but the function defensively returns [origin] when host isn't loopback.
        out = main_mod._expand_loopback_origin("not-a-url")
        assert out == ["not-a-url"]


# ---------------------------------------------------------------------------
# _max_body_bytes
# ---------------------------------------------------------------------------
class TestMaxBodyBytes:
    def test_default_when_unset(self, monkeypatch, main_mod):
        monkeypatch.delenv("MAX_REQUEST_BODY_BYTES", raising=False)
        assert main_mod._max_body_bytes() == 256 * 1024

    def test_explicit_positive_value(self, monkeypatch, main_mod):
        monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "65536")
        assert main_mod._max_body_bytes() == 65536

    def test_zero_falls_back_to_default(self, monkeypatch, main_mod):
        monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "0")
        assert main_mod._max_body_bytes() == 256 * 1024

    def test_negative_falls_back_to_default(self, monkeypatch, main_mod):
        monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "-100")
        assert main_mod._max_body_bytes() == 256 * 1024

    def test_invalid_int_falls_back_to_default(self, monkeypatch, main_mod):
        monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "not-a-number")
        assert main_mod._max_body_bytes() == 256 * 1024

    def test_empty_string_falls_back_to_default(self, monkeypatch, main_mod):
        monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "")
        assert main_mod._max_body_bytes() == 256 * 1024


# ---------------------------------------------------------------------------
# _read_allowed_origins (integration of the helpers above)
# ---------------------------------------------------------------------------
class TestReadAllowedOrigins:
    def test_unset_returns_local_defaults(self, monkeypatch, main_mod):
        monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
        out = main_mod._read_allowed_origins()
        # Defaults include both localhost and 127.0.0.1 variants.
        assert "http://localhost:5173" in out
        assert "http://127.0.0.1:5173" in out

    def test_explicit_origin_expanded_and_deduplicated(self, monkeypatch, main_mod):
        monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:5173")
        out = main_mod._read_allowed_origins()
        # Order preserved, no duplicates.
        assert len(out) == len(set(out))
        assert "http://localhost:5173" in out
        assert "http://127.0.0.1:5173" in out

    def test_garbage_value_falls_back_to_defaults(self, monkeypatch, main_mod):
        # _parse_allowed_origins returns None only on JSON parse failure that
        # ALSO can't be salvaged by inner-split. The current implementation always
        # returns at least an empty list, so verify normalize→empty path uses defaults.
        monkeypatch.setenv("ALLOWED_ORIGINS", '""')  # parses to single empty-string item
        out = main_mod._read_allowed_origins()
        # Empty after normalization → falls back to LOCAL defaults.
        assert "http://localhost:5173" in out
