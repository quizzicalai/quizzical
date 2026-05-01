"""§21 Phase 6 — content flagging primitives.

Pure helpers (no DB / no Redis) for the flagging endpoint:

- `hash_ip(ip)` — HMAC-SHA256(`FLAG_HMAC_SECRET`, ip). Returns 64-char
  hex. Salt-rotated by replacing the env secret. We never persist the
  raw IP (`AC-PRECOMP-FLAG-3`).
- `validate_reason_code(code)` — returns one of:
    - `"ok"`       → accepted, persist normally.
    - `"honeypot"` → silent drop (`AC-PRECOMP-SEC-7`).
    - `"unknown"` → 422 to the caller (`AC-PRECOMP-FLAG-1`).
- `clamp_reason_text(text)` — truncates to `MAX_REASON_TEXT` chars and
  scrubs obvious PII patterns (email, phone, IPv4) before storage
  (`AC-PRECOMP-FLAG-2`).
- `should_quarantine(distinct_ip_count, threshold)` — straight-forward.
- `is_abusive_ip(distinct_target_count_24h, limit)` — > 50 distinct
  targets in 24 h → True (`AC-PRECOMP-SEC-7`).
"""

from __future__ import annotations

import hmac
import re
from hashlib import sha256

# Allowed reason codes. The honeypot code looks like a normal one but is
# never advertised in the FE — only abusers / scripted scanners send it.
ALLOWED_REASON_CODES: frozenset[str] = frozenset(
    {
        "inappropriate",
        "inaccurate",
        "broken",
        "spam",
        "other",
    }
)

HONEYPOT_REASON_CODES: frozenset[str] = frozenset({"_admin", "_test_"})

MAX_REASON_TEXT = 280
ABUSIVE_DISTINCT_TARGET_LIMIT = 50
DEFAULT_QUARANTINE_THRESHOLD = 5
DEFAULT_QUARANTINE_WINDOW_HOURS = 24

# Conservative PII scrubs. We never claim full PII coverage — these are
# only the patterns most commonly leaked in user-typed reason text.
_PII_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[email]"),
    (re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "[ip]"),
    # Loose phone match — 7-15 digits with optional separators.
    (re.compile(r"\+?\d[\d\s().-]{6,15}\d"), "[phone]"),
)


def hash_ip(ip: str, *, secret: str | None) -> str:
    """HMAC-SHA256 of an IP under the configured secret. Returns the hex
    digest (64 chars). When `secret` is missing we still hash with an
    empty key so callers don't crash, but `secrets.assert_*` will have
    blocked startup in any real deployment."""
    key = (secret or "").encode("utf-8")
    return hmac.new(key, ip.encode("utf-8"), sha256).hexdigest()


def validate_reason_code(code: str) -> str:
    if code in ALLOWED_REASON_CODES:
        return "ok"
    if code in HONEYPOT_REASON_CODES:
        return "honeypot"
    return "unknown"


def clamp_reason_text(text: str | None) -> str | None:
    if text is None:
        return None
    s = text.strip()
    if not s:
        return None
    for pat, repl in _PII_PATTERNS:
        s = pat.sub(repl, s)
    if len(s) > MAX_REASON_TEXT:
        s = s[:MAX_REASON_TEXT]
    return s


def should_quarantine(distinct_ip_count: int, *, threshold: int) -> bool:
    """`AC-PRECOMP-FLAG-4` — quarantine after threshold distinct IPs flag
    the same target inside the configured window."""
    if threshold < 1:
        raise ValueError("threshold must be ≥ 1")
    return distinct_ip_count >= threshold


def is_abusive_ip(
    distinct_target_count_24h: int,
    *,
    limit: int = ABUSIVE_DISTINCT_TARGET_LIMIT,
) -> bool:
    """`AC-PRECOMP-SEC-7` — > N distinct targets in 24 h ⇒ shadow-discard."""
    return distinct_target_count_24h > limit
