"""§21 Phase 5 — rehost trigger.

When a `media_assets` row's `expires_at` is within the configured
`rehost_window_days`, the next worker pass should re-download the bytes
through the active provider so the URI stays valid past the upstream's
expiry. The trigger is intentionally side-effect-free; callers query
`needs_rehost(...)` and feed the asset into the writer themselves.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def needs_rehost(
    *,
    expires_at: datetime | None,
    now: datetime | None = None,
    window_days: int,
) -> bool:
    """True when `expires_at` is non-null and ≤ `window_days` from `now`.

    Rows without an expiry (`expires_at IS NULL`) are treated as
    permanent — they never qualify. Past-expiry rows (negative delta)
    obviously qualify too: they're already broken from the renderer's
    perspective."""
    if expires_at is None:
        return False
    if window_days < 0:
        raise ValueError("window_days must be ≥ 0")
    n = now or datetime.now(UTC)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return (expires_at - n) <= timedelta(days=window_days)
