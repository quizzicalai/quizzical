"""Server-Timing recorder (§17.4).

Provides a tiny, allocation-light per-request timing recorder so individual
request handlers (and middleware) can attribute slices of wall time to
named segments — DB, Redis, LLM calls, etc. — that surface to the browser
via the W3C ``Server-Timing`` response header.

Design:
  * Only the segment **name** and **total duration in ms** are emitted.
  * Names are validated so a stray header injection is impossible:
    ``[A-Za-z0-9_-]+`` and length ≤ 64.
  * Duplicate segments accumulate (so e.g. multiple DB calls produce a
    single ``db;dur=…`` slice).
  * Insertion order is preserved.
  * Negative durations are clamped to zero (defensive).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from starlette.requests import Request

_VALID_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")

_REQUEST_STATE_ATTR = "server_timing"


class TimingRecorder:
    """Accumulates ``name → total_ms`` segments for a single request."""

    __slots__ = ("_segments",)

    def __init__(self) -> None:
        self._segments: dict[str, float] = {}

    def record(self, name: str, duration_ms: float) -> None:
        """Add ``duration_ms`` to the named segment. Invalid names are dropped."""
        if not isinstance(name, str) or not _VALID_NAME.match(name):
            return
        try:
            d = float(duration_ms)
        except (TypeError, ValueError):
            return
        if d < 0 or d != d:  # negatives or NaN
            d = 0.0
        self._segments[name] = self._segments.get(name, 0.0) + d

    def to_header(self, *, app_dur_ms: float) -> str:
        """Render the W3C ``Server-Timing`` header value."""
        try:
            app_d = float(app_dur_ms)
        except (TypeError, ValueError):
            app_d = 0.0
        if app_d < 0 or app_d != app_d:
            app_d = 0.0
        parts = [f"app;dur={app_d:.1f}"]
        for name, total in self._segments.items():
            parts.append(f"{name};dur={total:.1f}")
        return ", ".join(parts)


def get_request_timing(request: "Request") -> TimingRecorder:
    """Return (or lazily create) the per-request ``TimingRecorder``."""
    state = request.state
    rec = getattr(state, _REQUEST_STATE_ATTR, None)
    if rec is None:
        rec = TimingRecorder()
        setattr(state, _REQUEST_STATE_ATTR, rec)
    return rec


__all__ = ["TimingRecorder", "get_request_timing"]
