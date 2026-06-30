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

import structlog

logger = structlog.get_logger(__name__)

MIN_SECRET_BYTES: int = 32

# Recognized NON-production env names. Anything else — including the
# deployment's own "azure" or a typo'd value — is treated as PRODUCTION so the
# weak-secret check fails CLOSED rather than silently skipping (P0-3). Mirrors
# app.core.config.NON_PROD_ENVS; kept inline so this security primitive carries
# no import-time coupling.
NON_PROD_ENVS: frozenset[str] = frozenset(
    {"local", "dev", "development", "test", "testing", "ci", "staging"}
)

# Back-compat alias (no longer used for the gate; retained for any importer).
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
    if audit.environment in NON_PROD_ENVS:
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


_TURNSTILE_PLACEHOLDER = "your_turnstile_secret_key"


# --- LLM provider key fail-closed (Hitlist #9) ------------------------------
#
# The live agent loop depends on the providers wired into
# `quizzical.llm.tools.*.model`. We mirror `assert_turnstile_enforced_or_fail_closed`
# but stay FALLBACK-AWARE so we don't contradict the codebase's graceful
# degradation:
#
#   * `app.services.llm_service._substitute_model_if_key_missing` substitutes a
#     gpt-* / openai model with `gemini/gemini-flash-latest` when OPENAI_API_KEY
#     is absent, and `.github/workflows/api-deploy.yml` DELIBERATELY skips an
#     empty OPENAI secret ("runtime fallback will activate"). So a missing
#     OPENAI_API_KEY must NOT be a hard boot failure when GEMINI_API_KEY is
#     present — we log a WARNING and allow boot.
#   * GEMINI_API_KEY is the universal fallback target AND `profile_batch_writer`
#     uses a gemini model directly with NO fallback. So a referenced gemini
#     provider with a missing key (and no further fallback) DOES fail closed.
#
# Rule: a provider whose key is missing/placeholder fails CLOSED only when NO
# fallback provider key is available; otherwise we WARN and allow boot. Non-prod
# always returns without raising. Never logs the key values themselves.

# Provider -> the env var that must be non-empty/non-placeholder for it. The
# model-string prefixes mirror app.services.llm_service._substitute_model_if_key_missing
# and the `gemini/...` LiteLLM convention used throughout appconfig.
_PROVIDER_ENV_VAR: dict[str, str] = {
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}

# Provider -> the provider its runtime fallback model targets, mirroring
# llm_service._PROVIDER_FALLBACK_MODEL ("gemini/gemini-flash-latest"). A provider
# with no entry here has NO runtime fallback (e.g. gemini itself).
_PROVIDER_FALLBACK_PROVIDER: dict[str, str] = {
    "openai": "gemini",
    "groq": "gemini",
    "anthropic": "gemini",
}

# Common placeholder substrings shipped in .env.example; treat as "unset".
_KEY_PLACEHOLDER_MARKERS: tuple[str, ...] = (
    "your_",
    "changeme",
    "replace_me",
    "placeholder",
    "xxx",
)


def _provider_for_model(model: str | None) -> str | None:
    """Map a LiteLLM model string to its provider key bucket, or None."""
    ml = (model or "").strip().lower()
    if not ml:
        return None
    if ml.startswith(("gpt-", "openai/", "o3", "o4")):
        return "openai"
    if ml.startswith(("gemini/", "google/", "vertex_ai/")):
        return "gemini"
    if ml.startswith("groq/"):
        return "groq"
    if ml.startswith(("anthropic/", "claude-")):
        return "anthropic"
    return None


def _key_present(value: str | None) -> bool:
    s = (value or "").strip()
    if not s:
        return False
    low = s.lower()
    return not any(marker in low for marker in _KEY_PLACEHOLDER_MARKERS)


def assert_llm_provider_keys_or_fail_closed(
    *,
    environment: str | None,
    tool_models: dict[str, str],
    env_lookup,
) -> None:
    """Fail CLOSED in prod-class envs when a referenced LLM provider key is
    missing or a placeholder AND no runtime fallback key is available.

    FALLBACK-AWARE (see module note): a provider whose key is missing fails
    closed only when no fallback provider key exists. A missing OPENAI_API_KEY
    with GEMINI_API_KEY present is allowed (runtime substitutes gemini) and only
    WARNs. A missing GEMINI_API_KEY (the universal fallback target, also used
    directly by profile_batch_writer with no fallback) DOES fail closed.

    ``tool_models`` maps tool name -> model string (e.g. ``settings.llm_tools``
    flattened to ``{name: cfg.model}``). ``env_lookup`` is a callable taking an
    env-var name and returning its value (typically ``os.getenv``); injected so
    the check is unit-testable without touching the process environment.

    Never logs or returns the key values themselves.
    """
    if (environment or "local").strip().lower() in NON_PROD_ENVS:
        return

    # Collect the distinct providers referenced by the configured models, and
    # which tools reference each (for clear log / error messages).
    providers: dict[str, list[str]] = {}
    for tool_name, model in (tool_models or {}).items():
        provider = _provider_for_model(model)
        if provider is None:
            continue
        providers.setdefault(provider, []).append(tool_name)

    def _has_key(prov: str) -> bool:
        env_var = _PROVIDER_ENV_VAR.get(prov)
        return bool(env_var) and _key_present(env_lookup(env_var))

    missing: list[str] = []
    for provider, tools in sorted(providers.items()):
        env_var = _PROVIDER_ENV_VAR.get(provider)
        if not env_var:
            continue
        if _has_key(provider):
            continue
        # Key absent — is there a runtime fallback whose key IS present?
        fallback = _PROVIDER_FALLBACK_PROVIDER.get(provider)
        if fallback and _has_key(fallback):
            logger.warning(
                "llm_provider_keys.fallback_active",
                provider=provider,
                missing_env=env_var,
                fallback_provider=fallback,
                fallback_env=_PROVIDER_ENV_VAR.get(fallback),
                tools=sorted(tools),
                detail=(
                    f"{env_var} not set; runtime will fall back to {fallback}. "
                    "Allowing boot (graceful degradation)."
                ),
            )
            continue
        missing.append(f"{env_var} (provider={provider}, tools={sorted(tools)})")

    if missing:
        raise RuntimeError(
            "Refusing to start: LLM provider key(s) missing/placeholder with no "
            "available runtime fallback, for models referenced in "
            "quizzical.llm.tools.*.model: "
            + "; ".join(missing)
            + ". Wire the key(s) via the deployment environment / Key Vault."
        )


def assert_turnstile_enforced_or_fail_closed(
    *,
    environment: str | None,
    enabled: bool,
    secret: str | None,
) -> None:
    """Fail CLOSED in production when bot-protection is not actually enforced.

    Turnstile is the only hard gate on the paid /quiz/start (and /feedback)
    path. Previously nothing asserted it was on in prod, so a deploy with
    ENABLE_TURNSTILE off — or a missing/placeholder secret — would silently
    accept any quiz, exposing the #1 cost-abuse risk. Non-prod returns without
    raising (developer ergonomics). Never logs the secret value.
    """
    if (environment or "local").strip().lower() in NON_PROD_ENVS:
        return
    s = (secret or "").strip()
    if not enabled or not s or s == _TURNSTILE_PLACEHOLDER:
        raise RuntimeError(
            "Refusing to start: Turnstile bot-protection must be enforced in "
            "production. Set ENABLE_TURNSTILE=true and a real "
            "TURNSTILE_SECRET_KEY (not the placeholder)."
        )
