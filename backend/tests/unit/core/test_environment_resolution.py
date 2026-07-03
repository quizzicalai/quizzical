"""P0-3 — single authoritative environment + fail-closed classification.

The deployed Container App sets APP_ENVIRONMENT=azure but config previously
ignored it, pinning settings.app.environment to the YAML's "local" and
silently disabling every prod-only gate (proven live: no HSTS header). These
tests pin: (1) the OS var wins over the baked YAML, and (2) unknown env names
(incl. "azure") are treated as PRODUCTION so security fails closed.
"""
from __future__ import annotations

import pytest

from app.core.config import NON_PROD_ENVS, get_settings, is_production
from app.services.precompute.secrets import (
    MIN_SECRET_BYTES,
    assert_precompute_secrets_or_fail_closed,
    assert_turnstile_enforced_or_fail_closed,
)


class TestIsProduction:
    @pytest.mark.parametrize(
        "env", ["local", "dev", "development", "test", "testing", "LOCAL", "Dev"]
    )
    def test_recognized_non_prod_is_not_production(self, env):
        assert is_production(env) is False
        assert env.strip().lower() in NON_PROD_ENVS

    # "ci"/"staging" are production-classified per the 2026-07-02 owner decision.
    @pytest.mark.parametrize(
        "env",
        ["azure", "production", "prod", "PROD", "unknown", "", None, "ci", "staging"],
    )
    def test_unknown_or_prod_is_production(self, env):
        # azure / blank / typo all fail CLOSED (treated as production).
        assert is_production(env) is True


def test_os_env_overrides_baked_yaml(monkeypatch):
    monkeypatch.setenv("APP_ENVIRONMENT", "azure")
    get_settings.cache_clear()
    try:
        s = get_settings()
        assert s.app.environment == "azure"
        assert s.APP_ENVIRONMENT == "azure"
    finally:
        get_settings.cache_clear()


def test_azure_fails_closed_on_weak_secrets():
    # The live deployment runs as "azure"; it MUST be treated as production by
    # the weak-secret startup guard.
    with pytest.raises(RuntimeError) as exc:
        assert_precompute_secrets_or_fail_closed(
            environment="azure", operator_token=None, flag_hmac_secret=None
        )
    assert "OPERATOR_TOKEN" in str(exc.value)


def test_azure_passes_with_strong_secrets():
    strong = "z" * MIN_SECRET_BYTES
    audit = assert_precompute_secrets_or_fail_closed(
        environment="azure", operator_token=strong, flag_hmac_secret=strong + "x"
    )
    assert audit.all_ok is True


class TestTurnstileFailClosed:
    @pytest.mark.parametrize("env", ["local", "dev", "test"])
    def test_non_prod_never_raises(self, env):
        # Even disabled + no secret is fine in non-prod (dev ergonomics).
        assert_turnstile_enforced_or_fail_closed(environment=env, enabled=False, secret=None)

    def test_prod_disabled_fails_closed(self):
        with pytest.raises(RuntimeError):
            assert_turnstile_enforced_or_fail_closed(
                environment="azure", enabled=False, secret="real-secret-value"
            )

    @pytest.mark.parametrize("secret", [None, "", "your_turnstile_secret_key"])
    def test_prod_enabled_but_bad_secret_fails_closed(self, secret):
        with pytest.raises(RuntimeError):
            assert_turnstile_enforced_or_fail_closed(
                environment="azure", enabled=True, secret=secret
            )

    def test_prod_enabled_with_real_secret_passes(self):
        # Matches the live deployment (ENABLE_TURNSTILE=true + KV secret).
        assert_turnstile_enforced_or_fail_closed(
            environment="azure", enabled=True, secret="0x4AAAA-real-secret"
        )
