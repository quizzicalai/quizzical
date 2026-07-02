"""Recency-window filtering for the reply pipeline. Stdlib-only.

Owner rule: at 6 reply cycles/day we only ever consider posts from the LAST
window (4 hours). Anything older is stale — replying to yesterday's post
reads as botty.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

REPLY_WINDOW_HOURS = 4.0


def parse_x_timestamp(value: str) -> datetime:
    """Parse an X API ISO-8601 timestamp (e.g. '2026-07-02T15:04:05.000Z')."""
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def within_window(
    created_at: datetime | str,
    now: datetime | None = None,
    window_hours: float = REPLY_WINDOW_HOURS,
) -> bool:
    """True iff created_at falls inside [now - window, now].

    Future timestamps (clock skew, scheduled posts) are NOT within the window.
    """
    if isinstance(created_at, str):
        created_at = parse_x_timestamp(created_at)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    if created_at > now + timedelta(minutes=2):  # small skew allowance
        return False
    return (now - created_at) <= timedelta(hours=window_hours)


def window_start(now: datetime | None = None, window_hours: float = REPLY_WINDOW_HOURS) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now - timedelta(hours=window_hours)
