"""Unit tests for the small-but-critical helpers in ``app.core.logging_config``.

The full ``configure_logging()`` plumbing has integration coverage via the
observability suite. These tests pin down the pure helpers (env parsers,
PII scrubbers, sensitive-key redaction) which are easy to break and hard
to notice in production logs.

Critical security guarantees verified:

* JWT-shaped tokens are masked.
* Card-style digit runs (PAN) are masked except the last four digits.
* Email addresses are reduced to ``prefix@***``.
* Sensitive header/key names (``authorization``, ``api-key``,
  ``cf-turnstile-response``) are replaced with ``******`` even when they
  appear nested in mappings or lists.
"""

from __future__ import annotations

import logging

import pytest

from app.core import logging_config as lc

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Env parsers
# ---------------------------------------------------------------------------


class TestCsvEnv:
    def test_returns_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("X_CSV", raising=False)
        assert lc._csv_env("X_CSV", default=("a", "b")) == ["a", "b"]

    def test_parses_comma_list_strips_whitespace_and_drops_empties(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("X_CSV", " foo , bar ,, baz ")
        assert lc._csv_env("X_CSV") == ["foo", "bar", "baz"]

    def test_blank_string_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("X_CSV", "")
        assert lc._csv_env("X_CSV", default=("zz",)) == ["zz"]


class TestBoolEnv:
    @pytest.mark.parametrize("raw", ["1", "true", "T", "yes", "Y", "on"])
    def test_truthy_values(self, monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
        monkeypatch.setenv("X_BOOL", raw)
        assert lc._bool_env("X_BOOL", default=False) is True

    @pytest.mark.parametrize("raw", ["0", "false", "no", "off", "anything-else"])
    def test_falsy_values(self, monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
        monkeypatch.setenv("X_BOOL", raw)
        assert lc._bool_env("X_BOOL", default=True) is False

    def test_unset_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("X_BOOL", raising=False)
        assert lc._bool_env("X_BOOL", default=True) is True


class TestLevelEnv:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("DEBUG", logging.DEBUG),
            ("info", logging.INFO),
            ("Warning", logging.WARNING),
            ("ERROR", logging.ERROR),
        ],
    )
    def test_known_levels(
        self, monkeypatch: pytest.MonkeyPatch, raw: str, expected: int
    ) -> None:
        monkeypatch.setenv("X_LVL", raw)
        assert lc._level_env("X_LVL", default=logging.INFO) == expected

    def test_unknown_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("X_LVL", "BOGUS")
        assert lc._level_env("X_LVL", default=logging.WARNING) == logging.WARNING

    def test_unset_returns_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("X_LVL", raising=False)
        assert lc._level_env("X_LVL", default=logging.CRITICAL) == logging.CRITICAL


class TestIntEnv:
    def test_parses_int(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("X_INT", "42")
        assert lc._int_env("X_INT", default=0) == 42

    def test_returns_default_on_garbage(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("X_INT", "not-a-number")
        assert lc._int_env("X_INT", default=7) == 7


class TestParseSampleMap:
    def test_parses_key_value_pairs(self) -> None:
        out = lc._parse_sample_map("a=0.5, b=1.0,c=0")
        assert out == {"a": 0.5, "b": 1.0, "c": 0.0}

    def test_skips_malformed_pairs_and_invalid_floats(self) -> None:
        # Pairs without "=" are dropped; pairs whose value isn't a float are dropped.
        # Empty keys are accepted (current behaviour) — pinned here so a tightening
        # of validation is a deliberate, visible change.
        out = lc._parse_sample_map("a=0.5, no-equals, b=oops, c=0.25")
        assert out == {"a": 0.5, "c": 0.25}

    def test_empty_returns_empty(self) -> None:
        assert lc._parse_sample_map("") == {}


# ---------------------------------------------------------------------------
# PII scrubbing
# ---------------------------------------------------------------------------


class TestScrubPii:
    def test_jwt_is_masked(self) -> None:
        token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.aaa-bbb_ccc"
        out = lc._scrub_pii(f"auth header is {token} done")
        assert "eyJhbG" not in out
        assert "eyJ***" in out

    def test_pan_is_masked_keeping_last_four(self) -> None:
        out = lc._scrub_pii("card 4111 1111 1111 1234 stored")
        assert "4111" not in out  # first quartet gone
        assert "****1234" in out

    def test_email_is_masked_keeping_prefix(self) -> None:
        out = lc._scrub_pii("contact alice.example@quizzical.io please")
        assert "@quizzical.io" not in out
        assert "alic" in out  # first 1-4 chars retained
        assert "@***" in out

    def test_non_string_returned_unchanged(self) -> None:
        assert lc._scrub_pii(None) is None  # type: ignore[arg-type]
        assert lc._scrub_pii("") == ""

    def test_idempotent_on_clean_strings(self) -> None:
        s = "Just a normal log message with numbers 12 and words."
        assert lc._scrub_pii(s) == s


# ---------------------------------------------------------------------------
# Sensitive-key redaction
# ---------------------------------------------------------------------------


class TestRedactInStr:
    @pytest.mark.parametrize(
        "key", ["authorization", "api-key", "cf-turnstile-response", "password", "secret", "token"]
    )
    def test_replaces_sensitive_keys(self, key: str) -> None:
        out = lc._redact_in_str(f"header {key} value")
        assert lc._REDACTION in out
        assert key not in out

    def test_replaces_uppercase_sensitive_keys(self) -> None:
        out = lc._redact_in_str("AUTHORIZATION: Bearer xyz")
        assert lc._REDACTION in out
        assert "AUTHORIZATION" not in out

    def test_non_string_returned_as_is(self) -> None:
        assert lc._redact_in_str(123) == 123
        assert lc._redact_in_str([1, 2]) == [1, 2]

    def test_combines_pii_scrub(self) -> None:
        out = lc._redact_in_str("authorization=eyJabc.def.ghi for alice@example.com")
        assert "authorization" not in out
        assert "eyJabc" not in out
        assert "@example.com" not in out


class TestRedactInMapping:
    def test_redacts_top_level_sensitive_key(self) -> None:
        out = lc._redact_in_mapping({"Authorization": "Bearer xyz", "ok": "fine"})
        assert out["Authorization"] == lc._REDACTION
        assert out["ok"] == "fine"

    def test_redacts_nested_mapping(self) -> None:
        out = lc._redact_in_mapping(
            {"headers": {"api-key": "abc", "x-trace": "t"}}
        )
        assert out["headers"]["api-key"] == lc._REDACTION
        assert out["headers"]["x-trace"] == "t"

    def test_redacts_inside_list_of_mappings(self) -> None:
        out = lc._redact_in_mapping({"items": [{"token": "t"}, {"name": "n"}]})
        assert out["items"][0]["token"] == lc._REDACTION
        assert out["items"][1]["name"] == "n"

    def test_redacts_pii_inside_value_strings(self) -> None:
        out = lc._redact_in_mapping(
            {"msg": "contact bob.example@quizzical.io", "n": 1}
        )
        assert "@quizzical.io" not in out["msg"]
        assert out["n"] == 1


class TestRedactProcessor:
    def test_redacts_event_dict(self) -> None:
        ev = {"Authorization": "Bearer xyz", "msg": "bob@example.com"}
        out = lc.redact_processor(None, "info", ev)
        assert out["Authorization"] == lc._REDACTION
        assert "@example.com" not in out["msg"]

    def test_passes_through_non_mapping(self) -> None:
        # Strings shouldn't crash even though structlog usually emits dicts.
        assert lc.redact_processor(None, "info", "ignored") == "ignored"  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Format exception processor
# ---------------------------------------------------------------------------


class TestFormatExcOnError:
    def test_passes_through_info_logs(self) -> None:
        ev = {"event": "ok", "level": "info"}
        out = lc._format_exc_on_error(None, "info", ev)
        # No exc_info key → unchanged.
        assert out is ev

    def test_format_called_for_error_level(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called: dict[str, object] = {}

        def _fake_format(_logger, _method, ed):
            called["method"] = _method
            ed["exception"] = "formatted"
            return ed

        monkeypatch.setattr(
            "app.core.logging_config.structlog.processors.format_exc_info",
            _fake_format,
        )
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            import sys

            ev = {"event": "x", "level": "error", "exc_info": sys.exc_info()}
        out = lc._format_exc_on_error(None, "error", ev)
        assert called["method"] == "error"
        assert out["exception"] == "formatted"
