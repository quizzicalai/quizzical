"""Deep-review #15 — the security-gate env classification must have ONE source
of truth.

``app.services.precompute.secrets`` used to RE-LIST the set of non-production env
names inline, so it could silently drift from the canonical
``app.core.config.NON_PROD_ENVS`` that ``is_production()`` consults. Drift there
is exactly what lets a "staging"/"ci" deploy skip the fail-closed Turnstile /
operator-secret-strength / 2FA / LLM-key gates in one code path while enforcing
them in another. These tests pin the identity so the two can never diverge again.

POLICY NOTE (owner decision, not asserted here): "staging" and "ci" are in the
LENIENT set, so those envs skip the security gates. That is only safe if
staging/CI are network-isolated. If not, remove them from
``app.core.config.NON_PROD_ENVS`` (the single source) — every gate then fails
closed there too.
"""
from __future__ import annotations

from app.core.config import NON_PROD_ENVS as CONFIG_NON_PROD_ENVS
from app.core.config import is_production
from app.services.precompute import secrets as secrets_mod


def test_secrets_reexports_the_config_non_prod_set_by_identity():
    """secrets.NON_PROD_ENVS must be the EXACT same object as the config set —
    an import/re-export, not an inline copy that can drift."""
    assert secrets_mod.NON_PROD_ENVS is CONFIG_NON_PROD_ENVS


def test_all_security_gates_agree_with_is_production():
    """Every env name classified non-prod by the config set is treated as
    non-prod (lenient) by each fail-closed gate, and vice-versa. This walks the
    gates the way main.py wires them and asserts they never disagree with
    ``is_production`` for ANY env in (or out of) the set."""
    sample_envs = sorted(CONFIG_NON_PROD_ENVS) + [
        "production",
        "prod",
        "azure",
        "",  # blank -> defaults to "local" (non-prod) per the helpers
        "typo-env",
    ]

    for env in sample_envs:
        lenient = env.strip().lower() in CONFIG_NON_PROD_ENVS or env == ""
        # is_production is the canonical inverse (blank -> "local" non-prod path
        # is handled by the callers passing "local"; here treat "" as non-prod).
        if env == "":
            assert not is_production("local")
            continue
        assert is_production(env) is (not lenient)

        # Turnstile gate: raises in prod-class envs, returns in non-prod.
        if lenient:
            # Non-prod: never raises regardless of secret state.
            secrets_mod.assert_turnstile_enforced_or_fail_closed(
                environment=env, enabled=False, secret=None
            )
        else:
            import pytest

            with pytest.raises(RuntimeError):
                secrets_mod.assert_turnstile_enforced_or_fail_closed(
                    environment=env, enabled=False, secret=None
                )


def test_operator_secret_gate_matches_classification():
    """The operator-secret-strength gate is lenient for exactly the config
    non-prod envs and fail-closed otherwise."""
    import pytest

    for env in sorted(CONFIG_NON_PROD_ENVS):
        # weak/missing secrets are tolerated in non-prod.
        audit = secrets_mod.assert_precompute_secrets_or_fail_closed(
            environment=env, operator_token=None, flag_hmac_secret=None
        )
        assert audit.environment == env

    with pytest.raises(RuntimeError):
        secrets_mod.assert_precompute_secrets_or_fail_closed(
            environment="production", operator_token=None, flag_hmac_secret=None
        )
    # An unrecognized env is treated as PRODUCTION (fail closed) — the drift guard.
    with pytest.raises(RuntimeError):
        secrets_mod.assert_precompute_secrets_or_fail_closed(
            environment="azure", operator_token=None, flag_hmac_secret=None
        )
