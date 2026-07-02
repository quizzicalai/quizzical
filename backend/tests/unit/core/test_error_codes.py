"""Whimsical error-code taxonomy (owner request, 2026-06-30).

Verifies the single-source-of-truth registry: well-formedness, the
notify-support allusion contract, dozens of codes, status/spec mapping, and the
backward-compatible legacy SCREAMING_SNAKE mapping.
"""

from __future__ import annotations

import app.core.error_codes as ec


def test_registry_has_dozens_of_codes() -> None:
    specs = ec.all_specs()
    # Owner brief: "dozens" of codes.
    assert len(specs) >= 30, f"expected dozens of codes, got {len(specs)}"


def test_every_code_is_well_formed() -> None:
    for code, spec in ec.all_specs().items():
        assert code == spec.code
        assert code.startswith("QF-")
        assert spec.internal_description.strip()
        assert spec.whimsical_message.strip()
        assert 100 <= spec.http_status <= 599
        assert isinstance(spec.severity, ec.Severity)


def test_codes_are_unique() -> None:
    codes = list(ec.all_specs().keys())
    assert len(codes) == len(set(codes))


def test_catch_all_exists_and_notifies() -> None:
    spec = ec.get_spec(ec.QF_UNKNOWN)
    assert spec.code == "QF-UNKNOWN"
    assert spec.notify_support is True
    assert spec.http_status == 500


def test_unknown_code_falls_back_to_catch_all() -> None:
    spec = ec.get_spec("QF-DOES-NOT-EXIST")
    assert spec.code == ec.QF_UNKNOWN
    # None also falls back.
    assert ec.get_spec(None).code == ec.QF_UNKNOWN


def test_notify_codes_allude_to_notification() -> None:
    """Owner contract: notify_support=True copy must allude to support being
    alerted; non-notify copy must NOT claim a notification."""
    for spec in ec.all_specs().values():
        alludes = ec._alludes_to_notification(spec.whimsical_message)
        if spec.notify_support:
            assert alludes, f"{spec.code} should allude to notification"
        else:
            assert not alludes, f"{spec.code} should NOT claim a notification"


def test_whimsical_messages_are_not_raw_technical() -> None:
    """The user-facing copy must not leak raw technical terms."""
    banned = ("traceback", "exception", "stack", "null pointer", "500 internal", "sqlalchemy")
    for spec in ec.all_specs().values():
        low = spec.whimsical_message.lower()
        for term in banned:
            assert term not in low, f"{spec.code} leaks technical term '{term}'"


def test_status_to_spec_mapping() -> None:
    assert ec.spec_for_status(404).code == ec.QF_QUIZ_NOT_FOUND
    assert ec.spec_for_status(429).code == ec.QF_RATE_LIMITED
    assert ec.spec_for_status(409).code == ec.QF_SESSION_BUSY
    assert ec.spec_for_status(504).code == ec.QF_AGENT_TIMEOUT
    assert ec.spec_for_status(500).code == ec.QF_UNKNOWN
    # Unenumerated 4xx -> generic bad-request; 5xx -> catch-all.
    assert ec.spec_for_status(418).code == ec.QF_BAD_REQUEST
    assert ec.spec_for_status(599).code == ec.QF_UNKNOWN


def test_legacy_error_code_mapping_is_backward_compatible() -> None:
    specs = ec.all_specs()
    assert ec.legacy_error_code(specs[ec.QF_QUIZ_NOT_FOUND]) == "NOT_FOUND"
    assert ec.legacy_error_code(specs[ec.QF_SESSION_BUSY]) == "SESSION_BUSY"
    assert ec.legacy_error_code(specs[ec.QF_RATE_LIMITED]) == "RATE_LIMITED"
    assert ec.legacy_error_code(specs[ec.QF_PAYLOAD_TOO_LARGE]) == "PAYLOAD_TOO_LARGE"
    # A spec with no explicit legacy mapping derives from status.
    assert ec.legacy_error_code(specs[ec.QF_AGENT_TIMEOUT]) == "INTERNAL_SERVER_ERROR"


def test_representative_codes_present() -> None:
    """All the real failure modes from the brief have a code."""
    required = [
        ec.QF_AGENT_TIMEOUT,
        ec.QF_LLM_PROVIDER_DOWN,
        ec.QF_LLM_RATE_LIMITED,
        ec.QF_LLM_INVALID_OUTPUT,
        ec.QF_IMAGE_GEN_FAILED,
        ec.QF_IMAGE_GEN_TIMEOUT,
        ec.QF_CONFIG_LOAD_FAILED,
        ec.QF_DB_UNAVAILABLE,
        ec.QF_DB_TIMEOUT,
        ec.QF_REDIS_DOWN,
        ec.QF_TURNSTILE_FAILED,
        ec.QF_RATE_LIMITED,
        ec.QF_COST_CEILING,
        ec.QF_QUIZ_NOT_FOUND,
        ec.QF_RESULT_NOT_FOUND,
        ec.QF_INVALID_CATEGORY,
        ec.QF_PRECOMPUTE_FAILED,
        ec.QF_UNKNOWN,
    ]
    specs = ec.all_specs()
    for code in required:
        assert code in specs


def test_sample_voice_alludes_to_cause() -> None:
    """Spot-check the on-brand voice examples from the brief."""
    specs = ec.all_specs()
    assert "quiz-brain" in specs[ec.QF_AGENT_TIMEOUT].whimsical_message
    assert "muses" in specs[ec.QF_LLM_PROVIDER_DOWN].whimsical_message
