"""§21 Phase 3 — operator/flag secret hygiene (`AC-PRECOMP-SEC-9`).

Both `OPERATOR_TOKEN` (admin endpoint bearer) and `FLAG_HMAC_SECRET`
(community-flag IP hashing key) MUST be at least 32 bytes of entropy when
the application runs in a production environment. We:

1. Refuse to start if either is missing or too short in `production`;
2. Allow weak / unset secrets in `local` / `dev` / `test` / `staging` for
   developer ergonomics — admin endpoints that need them simply 401/403.

The check is intentionally separate from `Settings` so it can be wired into
the FastAPI lifespan and into a security test that exercises the
fail-closed branch without booting the whole app.
"""

from __future__ import annotations

from dataclasses import dataclass

MIN_SECRET_BYTES: int = 32

PROD_ENVS: frozenset[str] = frozenset({"production", "prod"})


@dataclass(frozen=True)
class SecretAudit:
    """Result of a single startup audit pass."""

    environment: str
    operator_token_ok: bool
    flag_hmac_ok: bool

    @property
    def all_ok(self) -> bool:
        return self.operator_token_ok and self.flag_hmac_ok


def _strong(value: str | None) -> bool:
    """A secret is "strong enough" iff it is a string of at least
    `MIN_SECRET_BYTES` UTF-8 bytes. We deliberately do not impose a
    character class — operators may use base64 / hex / random words."""
    if not value or not isinstance(value, str):
        return False
    return len(value.encode("utf-8")) >= MIN_SECRET_BYTES


def audit_precompute_secrets(
    *,
    environment: str | None,
    operator_token: str | None,
    flag_hmac_secret: str | None,
) -> SecretAudit:
    return SecretAudit(
        environment=(environment or "local").lower(),
        operator_token_ok=_strong(operator_token),
        flag_hmac_ok=_strong(flag_hmac_secret),
    )


def assert_precompute_secrets_or_fail_closed(
    *,
    environment: str | None,
    operator_token: str | None,
    flag_hmac_secret: str | None,
) -> SecretAudit:
    """Raise `RuntimeError` in production envs when either secret is
    missing or weak. Non-prod envs always return without raising.

    Never logs or returns the secret values themselves.
    """

    audit = audit_precompute_secrets(
        environment=environment,
        operator_token=operator_token,
        flag_hmac_secret=flag_hmac_secret,
    )
    if audit.environment not in PROD_ENVS:
        return audit
    if audit.all_ok:
        return audit

    missing: list[str] = []
    if not audit.operator_token_ok:
        missing.append("OPERATOR_TOKEN")
    if not audit.flag_hmac_ok:
        missing.append("FLAG_HMAC_SECRET")
    raise RuntimeError(
        "Refusing to start: required precompute operator secrets are "
        f"missing or weaker than {MIN_SECRET_BYTES} bytes: "
        f"{', '.join(missing)}. Set them in the deployment environment."
    )
