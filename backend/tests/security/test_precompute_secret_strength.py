"""§21 Phase 3 — secret strength fail-closed (`AC-PRECOMP-SEC-9`)."""

from __future__ import annotations

import pytest

from app.services.precompute.secrets import (
    MIN_SECRET_BYTES,
    SecretAudit,
    assert_precompute_secrets_or_fail_closed,
    audit_precompute_secrets,
)


_STRONG = "z" * MIN_SECRET_BYTES
_WEAK = "z" * (MIN_SECRET_BYTES - 1)


def test_audit_marks_strong_and_weak_correctly() -> None:
    a = audit_precompute_secrets(
        environment="local", operator_token=_STRONG, flag_hmac_secret=_WEAK,
    )
    assert isinstance(a, SecretAudit)
    assert a.operator_token_ok is True
    assert a.flag_hmac_ok is False
    assert a.all_ok is False


@pytest.mark.parametrize("env", ["local", "dev", "test", "staging"])
def test_non_prod_envs_never_raise_even_when_unset(env: str) -> None:
    # Both secrets unset → audit returns, no exception.
    audit = assert_precompute_secrets_or_fail_closed(
        environment=env, operator_token=None, flag_hmac_secret=None,
    )
    assert audit.environment == env
    assert audit.all_ok is False  # but the function must not raise


@pytest.mark.parametrize("env", ["production", "PROD"])
def test_prod_with_missing_secrets_fails_closed(env: str) -> None:
    with pytest.raises(RuntimeError) as exc:
        assert_precompute_secrets_or_fail_closed(
            environment=env, operator_token=None, flag_hmac_secret=None,
        )
    msg = str(exc.value)
    assert "OPERATOR_TOKEN" in msg
    assert "FLAG_HMAC_SECRET" in msg


def test_prod_with_weak_secrets_fails_closed() -> None:
    with pytest.raises(RuntimeError):
        assert_precompute_secrets_or_fail_closed(
            environment="production",
            operator_token=_WEAK,
            flag_hmac_secret=_WEAK,
        )


def test_prod_with_strong_secrets_passes() -> None:
    audit = assert_precompute_secrets_or_fail_closed(
        environment="production",
        operator_token=_STRONG,
        flag_hmac_secret=_STRONG + "x",
    )
    assert audit.all_ok is True
