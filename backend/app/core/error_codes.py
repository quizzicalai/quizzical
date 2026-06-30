"""Whimsical error-code taxonomy — the single source of truth (owner request,
2026-06-30).

Every failure mode in the app maps to a precise INTERNAL error code (prefix
``QF-``). Each code carries:

  * ``code``                 — the stable ``QF-...`` identifier (support triage).
  * ``internal_description`` — plain-English cause for logs / support.
  * ``whimsical_message``    — the USER-facing, on-brand message. It ALLUDES to
                               the cause (never raw technical detail). For codes
                               with ``notify_support=True`` the copy ALSO tells
                               the user help is already aware (e.g. "The Oracle
                               has been notified ✨") so we never claim a
                               notification we did not send.
  * ``http_status``          — the HTTP status the API returns.
  * ``severity``             — ``info`` | ``warning`` | ``error`` | ``critical``
                               (drives log level + whether support is paged).
  * ``notify_support``       — when True, a failure with this code fires a
                               fire-and-forget, rate-limited Resend email to
                               support@quafel.com (see ``services.support_notify``).

Design goals:
  * Adding a code is trivial — append one ``_register(...)`` entry below.
  * Backward-compatible: every spec also exposes a legacy SCREAMING_SNAKE
    ``error_code`` (the value the existing envelope's ``errorCode`` field used)
    so the FE error-contract tests keep passing. New ``QF-`` codes ride
    alongside, never replacing.
  * Self-validating at import time (unique codes, well-formed shape).

This module has NO heavy imports so it can be used anywhere (handlers, services,
the agent runner) without import cycles.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final


class Severity(str, Enum):
    """Operational severity — drives log level and whether support is paged."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass(frozen=True)
class ErrorCodeSpec:
    """Immutable spec for one whimsical error code."""

    code: str
    internal_description: str
    whimsical_message: str
    http_status: int
    severity: Severity = Severity.ERROR
    notify_support: bool = False
    # Legacy SCREAMING_SNAKE code kept in the envelope's ``errorCode`` field for
    # backward compatibility with the existing FE error contract. Defaults are
    # derived from ``http_status`` when omitted.
    legacy_error_code: str | None = None


# Owner request (2026-06-30): a notify_support=True code's user-facing copy must
# ALLUDE to support being alerted, and a non-notify code must NOT claim a
# notification. We embed the allusion directly in each whimsical_message (rather
# than auto-appending) so the phrasing varies tastefully per code. The import-
# time validator below enforces the contract by checking for any of these
# allusion markers; every notify message includes one and no non-notify message
# does.
_NOTIFY_ALLUSION_MARKERS: tuple[str, ...] = (
    "has been notified",
    "has been alerted",
    "is already on it",
    "is already looking",
    "knows about this",
)


def _alludes_to_notification(message: str) -> bool:
    low = message.lower()
    return any(marker in low for marker in _NOTIFY_ALLUSION_MARKERS)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, ErrorCodeSpec] = {}


def _register(
    code: str,
    *,
    internal_description: str,
    whimsical_message: str,
    http_status: int,
    severity: Severity = Severity.ERROR,
    notify_support: bool = False,
    legacy_error_code: str | None = None,
) -> str:
    if code in _REGISTRY:
        raise ValueError(f"Duplicate error code registered: {code}")
    _REGISTRY[code] = ErrorCodeSpec(
        code=code,
        internal_description=internal_description,
        whimsical_message=whimsical_message,
        http_status=http_status,
        severity=severity,
        notify_support=notify_support,
        legacy_error_code=legacy_error_code,
    )
    return code


# --- Catch-all -------------------------------------------------------------
QF_UNKNOWN = _register(
    "QF-UNKNOWN",
    internal_description="Unclassified / unexpected internal error (catch-all).",
    whimsical_message=(
        "Something tangled itself in the spellwork 🪄 — we couldn't quite finish "
        "that. The Oracle has been notified ✨, so please try again in a moment."
    ),
    http_status=500,
    severity=Severity.CRITICAL,
    notify_support=True,
    legacy_error_code="INTERNAL_SERVER_ERROR",
)

# --- Agent / quiz-brain ----------------------------------------------------
QF_AGENT_TIMEOUT = _register(
    "QF-AGENT-TIMEOUT",
    internal_description="The LangGraph agent run exceeded its time budget.",
    whimsical_message=(
        "Our quiz-brain wandered off chasing a thought 🧠 — give it another go."
    ),
    http_status=504,
    severity=Severity.WARNING,
)
QF_AGENT_FAILED = _register(
    "QF-AGENT-FAILED",
    internal_description="The agent run failed terminally (deterministic error).",
    whimsical_message=(
        "Our quiz-brain hit a knot it couldn't untangle 🧠 — the Oracle has been "
        "notified ✨. Please start a fresh quiz."
    ),
    http_status=422,
    severity=Severity.ERROR,
    notify_support=True,
)
QF_AGENT_NO_SYNOPSIS = _register(
    "QF-AGENT-NO-SYNOPSIS",
    internal_description="Agent produced no synopsis within the first-step budget.",
    whimsical_message=(
        "We couldn't dream up a story for that just now 💭 — try a different "
        "category or give it another whirl."
    ),
    http_status=503,
    severity=Severity.WARNING,
)
QF_AGENT_UNAVAILABLE = _register(
    "QF-AGENT-UNAVAILABLE",
    internal_description="The compiled agent graph is missing from app state.",
    whimsical_message=(
        "Our quiz-brain is still waking up ☕ — the Oracle has been notified ✨. "
        "Please try again shortly."
    ),
    http_status=503,
    severity=Severity.CRITICAL,
    notify_support=True,
)

# --- LLM provider ----------------------------------------------------------
QF_LLM_PROVIDER_DOWN = _register(
    "QF-LLM-PROVIDER-DOWN",
    internal_description="LLM provider unreachable / 5xx after retries.",
    whimsical_message=(
        "The muses are briefly unreachable 🎭 — our team is already on it. Try "
        "again in a moment."
    ),
    http_status=503,
    severity=Severity.ERROR,
    notify_support=True,
)
QF_LLM_RATE_LIMITED = _register(
    "QF-LLM-RATE-LIMITED",
    internal_description="LLM provider returned a rate-limit (429) after retries.",
    whimsical_message=(
        "The muses are a touch overwhelmed right now 🎭 — our team knows about "
        "this. Catch your breath and try again in a moment."
    ),
    http_status=503,
    severity=Severity.WARNING,
    notify_support=True,
)
QF_LLM_INVALID_OUTPUT = _register(
    "QF-LLM-INVALID-OUTPUT",
    internal_description="LLM returned unparseable / schema-invalid structured output.",
    whimsical_message=(
        "The muses spoke in riddles we couldn't quite read 🎭 — our team has been "
        "alerted. Please try again."
    ),
    http_status=502,
    severity=Severity.ERROR,
    notify_support=True,
)
QF_LLM_RESPONSE_TOO_LARGE = _register(
    "QF-LLM-RESPONSE-TOO-LARGE",
    internal_description="LLM response exceeded the configured size cap.",
    whimsical_message=(
        "The muses got a little too chatty 🎭 — our team is already looking into "
        "it. Please try that once more."
    ),
    http_status=502,
    severity=Severity.WARNING,
    notify_support=True,
)
QF_LLM_KEY_MISSING = _register(
    "QF-LLM-KEY-MISSING",
    internal_description="No usable LLM provider API key configured at runtime.",
    whimsical_message=(
        "Our muses have momentarily lost their voice 🎭 — the Oracle has been "
        "notified ✨. Please try again soon."
    ),
    http_status=503,
    severity=Severity.CRITICAL,
    notify_support=True,
)

# --- FAL image generation --------------------------------------------------
QF_IMAGE_GEN_FAILED = _register(
    "QF-IMAGE-GEN-FAILED",
    internal_description="FAL image generation failed after retries.",
    whimsical_message=(
        "Our illustrator smudged the canvas 🎨 — the words are all here, the "
        "pictures may take a moment longer. Our team has been alerted."
    ),
    http_status=502,
    severity=Severity.WARNING,
    notify_support=True,
)
QF_IMAGE_GEN_TIMEOUT = _register(
    "QF-IMAGE-GEN-TIMEOUT",
    internal_description="FAL image generation timed out.",
    whimsical_message=(
        "Our illustrator is still dabbing at the details 🎨 — the art may arrive "
        "a beat late."
    ),
    http_status=504,
    severity=Severity.INFO,
)
QF_PRECOMPUTE_FAILED = _register(
    "QF-PRECOMPUTE-FAILED",
    internal_description="Precompute pack hydration / short-circuit failed.",
    whimsical_message=(
        "Our prepared deck got shuffled 🃏 — our team is already on it. We'll "
        "deal you a fresh hand; try again."
    ),
    http_status=503,
    severity=Severity.WARNING,
    notify_support=True,
)

# --- Config ----------------------------------------------------------------
QF_CONFIG_LOAD_FAILED = _register(
    "QF-CONFIG-LOAD-FAILED",
    internal_description="Application configuration failed to load / parse.",
    whimsical_message=(
        "Our spellbook wouldn't open to the right page 📖 — the Oracle has been "
        "notified ✨. Please try again shortly."
    ),
    http_status=503,
    severity=Severity.CRITICAL,
    notify_support=True,
)

# --- Datastores ------------------------------------------------------------
QF_DB_UNAVAILABLE = _register(
    "QF-DB-UNAVAILABLE",
    internal_description="Database pool not ready / connection unavailable.",
    whimsical_message=(
        "Our library of memories is briefly closed 📚 — the Oracle has been "
        "notified ✨. Please try again in a moment."
    ),
    http_status=503,
    severity=Severity.CRITICAL,
    notify_support=True,
)
QF_DB_TIMEOUT = _register(
    "QF-DB-TIMEOUT",
    internal_description="Database query timed out.",
    whimsical_message=(
        "The archives took too long to answer 📚 — our team knows about this. "
        "Please try again."
    ),
    http_status=503,
    severity=Severity.ERROR,
    notify_support=True,
)
QF_REDIS_DOWN = _register(
    "QF-REDIS-DOWN",
    internal_description="Redis pool not ready / connection unavailable.",
    whimsical_message=(
        "Our short-term memory slipped for a second 🧩 — the Oracle has been "
        "notified ✨. Please try again."
    ),
    http_status=503,
    severity=Severity.CRITICAL,
    notify_support=True,
)

# --- Security / abuse ------------------------------------------------------
QF_TURNSTILE_FAILED = _register(
    "QF-TURNSTILE-FAILED",
    internal_description="Cloudflare Turnstile verification failed / token invalid.",
    whimsical_message=(
        "The gatekeeper needs to see your badge again 🛡️ — give it a quick "
        "refresh and retry."
    ),
    http_status=401,
    severity=Severity.INFO,
)
QF_TURNSTILE_MISSING = _register(
    "QF-TURNSTILE-MISSING",
    internal_description="Turnstile token absent / malformed / oversized.",
    whimsical_message=(
        "The gatekeeper didn't catch your badge 🛡️ — please refresh and try "
        "again."
    ),
    http_status=400,
    severity=Severity.INFO,
)
QF_TURNSTILE_VERIFY_ERROR = _register(
    "QF-TURNSTILE-VERIFY-ERROR",
    internal_description="Error calling Cloudflare to verify the Turnstile token.",
    whimsical_message=(
        "The gatekeeper's whistle wouldn't sound 🛡️ — the Oracle has been "
        "notified ✨. Please try again shortly."
    ),
    http_status=503,
    severity=Severity.ERROR,
    notify_support=True,
)
QF_RATE_LIMITED = _register(
    "QF-RATE-LIMITED",
    internal_description="Per-IP / global token-bucket rate limit exceeded.",
    whimsical_message=(
        "Whoa, speedster! 🏃 You're going a little fast for us — take a breath "
        "and try again shortly."
    ),
    http_status=429,
    severity=Severity.INFO,
    legacy_error_code="RATE_LIMITED",
)
QF_QUIZ_START_RATE_LIMITED = _register(
    "QF-QUIZ-START-RATE-LIMITED",
    internal_description="Per-IP /quiz/start throttle exceeded.",
    whimsical_message=(
        "So many quizzes, so little time! 🏃 You're starting them faster than we "
        "can conjure — please slow down a touch."
    ),
    http_status=429,
    severity=Severity.INFO,
    legacy_error_code="RATE_LIMITED",
)
QF_SESSION_ACTION_CAP = _register(
    "QF-SESSION-ACTION-CAP",
    internal_description="Per-session cost-bearing action cap reached.",
    whimsical_message=(
        "This quiz has lived a full and happy life 🎉 — time to start a fresh one!"
    ),
    http_status=429,
    severity=Severity.INFO,
    legacy_error_code="RATE_LIMITED",
)
QF_COST_CEILING = _register(
    "QF-COST-CEILING",
    internal_description="Global daily live-cost circuit breaker tripped.",
    whimsical_message=(
        "Our quiz-forge is glowing hot from a busy day 🔥 — the Oracle has been "
        "notified ✨. Please try again a little later."
    ),
    http_status=503,
    severity=Severity.CRITICAL,
    notify_support=True,
    legacy_error_code="SERVICE_UNAVAILABLE",
)

# --- Quiz flow / lookups ---------------------------------------------------
QF_QUIZ_NOT_FOUND = _register(
    "QF-QUIZ-NOT-FOUND",
    internal_description="Quiz session not found (Redis miss + no DB row).",
    whimsical_message=(
        "We couldn't find that quiz anywhere 🔍 — it may have wandered off or "
        "expired. Let's start a new one!"
    ),
    http_status=404,
    severity=Severity.INFO,
    legacy_error_code="NOT_FOUND",
)
QF_RESULT_NOT_FOUND = _register(
    "QF-RESULT-NOT-FOUND",
    internal_description="Shareable result not found by id (expired / never existed).",
    whimsical_message=(
        "That result has slipped into the mists 🌫️ — it may have expired or "
        "never existed. Try taking the quiz!"
    ),
    http_status=404,
    severity=Severity.INFO,
    legacy_error_code="NOT_FOUND",
)
QF_SESSION_BUSY = _register(
    "QF-SESSION-BUSY",
    internal_description="Concurrent request on the same session (single-flight lock).",
    whimsical_message=(
        "One thing at a time, please! ⏳ We're still working on your last move — "
        "give it a second and try again."
    ),
    http_status=409,
    severity=Severity.INFO,
    legacy_error_code="SESSION_BUSY",
)
QF_QUIZ_STALE_ANSWER = _register(
    "QF-QUIZ-STALE-ANSWER",
    internal_description="Out-of-order / stale answer submission.",
    whimsical_message=(
        "That answer arrived out of turn 🔀 — let's pick up where we left off."
    ),
    http_status=409,
    severity=Severity.INFO,
    legacy_error_code="CONFLICT",
)
QF_QUIZ_BAD_ANSWER = _register(
    "QF-QUIZ-BAD-ANSWER",
    internal_description="Invalid answer payload (index/option out of range or unselected).",
    whimsical_message=(
        "That choice didn't quite land ✋ — please pick one of the options shown."
    ),
    http_status=400,
    severity=Severity.INFO,
    legacy_error_code="BAD_REQUEST",
)
QF_INVALID_CATEGORY = _register(
    "QF-INVALID-CATEGORY",
    internal_description="Category failed validation (too short/long or malformed).",
    whimsical_message=(
        "That topic was a little too cryptic for us 🔮 — try a few more words."
    ),
    http_status=422,
    severity=Severity.INFO,
    legacy_error_code="VALIDATION_ERROR",
)
QF_BLOCKED_CATEGORY = _register(
    "QF-BLOCKED-CATEGORY",
    internal_description="Category blocked by safety / moderation policy.",
    whimsical_message=(
        "We can't spin a quiz on that one 🚫 — try a different topic and we'll "
        "happily oblige!"
    ),
    http_status=422,
    severity=Severity.WARNING,
    legacy_error_code="VALIDATION_ERROR",
)
QF_MALFORMED_RESULT = _register(
    "QF-MALFORMED-RESULT",
    internal_description="Stored final_result failed to validate when serving status.",
    whimsical_message=(
        "Your result came out a little blurry 🖼️ — the Oracle has been notified "
        "✨. Please start a fresh quiz."
    ),
    http_status=500,
    severity=Severity.ERROR,
    notify_support=True,
)
QF_MALFORMED_QUESTION = _register(
    "QF-MALFORMED-QUESTION",
    internal_description="A generated question failed to validate when serving status.",
    whimsical_message=(
        "That question got a little scrambled 🔡 — the Oracle has been notified "
        "✨. Please try again."
    ),
    http_status=500,
    severity=Severity.ERROR,
    notify_support=True,
)

# --- Generic transport-tier (envelope fallbacks for bare HTTPException) -----
QF_VALIDATION_ERROR = _register(
    "QF-VALIDATION-ERROR",
    internal_description="Request body failed schema validation.",
    whimsical_message=(
        "Something about that request didn't add up 🧮 — please check and try "
        "again."
    ),
    http_status=422,
    severity=Severity.INFO,
    legacy_error_code="VALIDATION_ERROR",
)
QF_BAD_REQUEST = _register(
    "QF-BAD-REQUEST",
    internal_description="Generic malformed client request.",
    whimsical_message=(
        "That request came out a bit jumbled 🧩 — please refresh and try again."
    ),
    http_status=400,
    severity=Severity.INFO,
    legacy_error_code="BAD_REQUEST",
)
QF_PAYLOAD_TOO_LARGE = _register(
    "QF-PAYLOAD-TOO-LARGE",
    internal_description="Request body exceeded the size limit.",
    whimsical_message=(
        "That's a wonderfully long message 📜 — a little too long for us. Trim it "
        "down and try again."
    ),
    http_status=413,
    severity=Severity.INFO,
    legacy_error_code="PAYLOAD_TOO_LARGE",
)
QF_UNAUTHORIZED = _register(
    "QF-UNAUTHORIZED",
    internal_description="Authentication required / failed.",
    whimsical_message=(
        "The gatekeeper needs to see your badge 🛡️ — please refresh and try "
        "again."
    ),
    http_status=401,
    severity=Severity.INFO,
    legacy_error_code="UNAUTHORIZED",
)
QF_FORBIDDEN = _register(
    "QF-FORBIDDEN",
    internal_description="Action not permitted for this caller.",
    whimsical_message=(
        "That door stays shut for now 🚪 — you don't have the key for this one."
    ),
    http_status=403,
    severity=Severity.INFO,
    legacy_error_code="FORBIDDEN",
)
QF_SERVICE_UNAVAILABLE = _register(
    "QF-SERVICE-UNAVAILABLE",
    internal_description="Generic service-unavailable (transient capacity / outage).",
    whimsical_message=(
        "We're catching our breath for a moment 🌬️ — please try again shortly."
    ),
    http_status=503,
    severity=Severity.WARNING,
    legacy_error_code="SERVICE_UNAVAILABLE",
)


# ---------------------------------------------------------------------------
# Public lookup API
# ---------------------------------------------------------------------------

_STATUS_TO_QF: Final[dict[int, str]] = {
    400: QF_BAD_REQUEST,
    401: QF_UNAUTHORIZED,
    403: QF_FORBIDDEN,
    404: QF_QUIZ_NOT_FOUND,
    409: QF_SESSION_BUSY,
    413: QF_PAYLOAD_TOO_LARGE,
    422: QF_VALIDATION_ERROR,
    429: QF_RATE_LIMITED,
    500: QF_UNKNOWN,
    503: QF_SERVICE_UNAVAILABLE,
    504: QF_AGENT_TIMEOUT,
}


def get_spec(code: str | None) -> ErrorCodeSpec:
    """Return the spec for ``code``, falling back to the catch-all when unknown."""
    if code and code in _REGISTRY:
        return _REGISTRY[code]
    return _REGISTRY[QF_UNKNOWN]


def spec_for_status(status_code: int) -> ErrorCodeSpec:
    """Map an HTTP status to its default QF spec (used when no explicit code)."""
    code = _STATUS_TO_QF.get(status_code)
    if code is not None:
        return _REGISTRY[code]
    if 400 <= status_code < 500:
        return _REGISTRY[QF_BAD_REQUEST]
    return _REGISTRY[QF_UNKNOWN]


def all_specs() -> dict[str, ErrorCodeSpec]:
    """Return a shallow copy of the whole registry (for tests / introspection)."""
    return dict(_REGISTRY)


def legacy_error_code(spec: ErrorCodeSpec) -> str:
    """The backward-compatible SCREAMING_SNAKE ``errorCode`` value for a spec."""
    if spec.legacy_error_code:
        return spec.legacy_error_code
    # Derive from status when not explicitly set.
    from app.core import errors as _errors  # local import: avoid cycle at module load

    return _errors.default_error_code_for_status(spec.http_status)


# ---------------------------------------------------------------------------
# Import-time self-validation (fails fast on a malformed registry).
# ---------------------------------------------------------------------------

def _validate_registry() -> None:
    for code, spec in _REGISTRY.items():
        assert code == spec.code, f"key/code mismatch: {code} != {spec.code}"
        assert code.startswith("QF-"), f"code must start with QF-: {code}"
        assert spec.internal_description.strip(), f"{code}: empty internal_description"
        assert spec.whimsical_message.strip(), f"{code}: empty whimsical_message"
        assert 100 <= spec.http_status <= 599, f"{code}: bad http_status"
        assert isinstance(spec.severity, Severity), f"{code}: bad severity"
        # Owner contract (2026-06-30): a notify_support code's user-facing copy
        # must ALLUDE to support being alerted; a non-notify code must NOT claim
        # a notification.
        alludes = _alludes_to_notification(spec.whimsical_message)
        if spec.notify_support:
            assert alludes, (
                f"{code}: notify_support=True but message does not allude to "
                "support being notified"
            )
        else:
            assert not alludes, (
                f"{code}: notify_support=False but message claims a notification"
            )


_validate_registry()


__all__ = [
    "ErrorCodeSpec",
    "Severity",
    "all_specs",
    "get_spec",
    "legacy_error_code",
    "spec_for_status",
    # Code constants (exported for precise wiring at failure sites).
    "QF_UNKNOWN",
    "QF_AGENT_TIMEOUT",
    "QF_AGENT_FAILED",
    "QF_AGENT_NO_SYNOPSIS",
    "QF_AGENT_UNAVAILABLE",
    "QF_LLM_PROVIDER_DOWN",
    "QF_LLM_RATE_LIMITED",
    "QF_LLM_INVALID_OUTPUT",
    "QF_LLM_RESPONSE_TOO_LARGE",
    "QF_LLM_KEY_MISSING",
    "QF_IMAGE_GEN_FAILED",
    "QF_IMAGE_GEN_TIMEOUT",
    "QF_PRECOMPUTE_FAILED",
    "QF_CONFIG_LOAD_FAILED",
    "QF_DB_UNAVAILABLE",
    "QF_DB_TIMEOUT",
    "QF_REDIS_DOWN",
    "QF_TURNSTILE_FAILED",
    "QF_TURNSTILE_MISSING",
    "QF_TURNSTILE_VERIFY_ERROR",
    "QF_RATE_LIMITED",
    "QF_QUIZ_START_RATE_LIMITED",
    "QF_SESSION_ACTION_CAP",
    "QF_COST_CEILING",
    "QF_QUIZ_NOT_FOUND",
    "QF_RESULT_NOT_FOUND",
    "QF_SESSION_BUSY",
    "QF_QUIZ_STALE_ANSWER",
    "QF_QUIZ_BAD_ANSWER",
    "QF_INVALID_CATEGORY",
    "QF_BLOCKED_CATEGORY",
    "QF_MALFORMED_RESULT",
    "QF_MALFORMED_QUESTION",
    "QF_VALIDATION_ERROR",
    "QF_BAD_REQUEST",
    "QF_PAYLOAD_TOO_LARGE",
    "QF_UNAUTHORIZED",
    "QF_FORBIDDEN",
    "QF_SERVICE_UNAVAILABLE",
]
