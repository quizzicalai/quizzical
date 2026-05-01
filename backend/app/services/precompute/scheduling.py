"""§21 Phase 7 — off-peak scheduling helper (`AC-PRECOMP-COST-5`).

`current_concurrency(now_utc, *, daytime, offpeak, window)` returns the
worker concurrency for a given UTC moment. The window string is
`"HH:MM-HH:MM"`; wrapping windows (e.g. `"22:00-04:00"`) are supported.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time


@dataclass(frozen=True)
class _Window:
    start: time
    end: time

    def contains(self, now: time) -> bool:
        if self.start <= self.end:
            return self.start <= now < self.end
        # Wrap-around midnight (e.g. 22:00-04:00).
        return now >= self.start or now < self.end


def parse_window(spec: str) -> _Window:
    """Parse `"HH:MM-HH:MM"` (UTC). Raises `ValueError` on malformed input."""
    try:
        a, b = spec.split("-", 1)
        sh, sm = a.split(":")
        eh, em = b.split(":")
        return _Window(
            start=time(int(sh), int(sm)),
            end=time(int(eh), int(em)),
        )
    except Exception as exc:
        raise ValueError(f"invalid offpeak window {spec!r}") from exc


def current_concurrency(
    now_utc: datetime,
    *,
    daytime: int,
    offpeak: int,
    window: str,
) -> int:
    """Return `offpeak` if `now_utc.time()` is inside `window`, else `daytime`."""
    win = parse_window(window)
    return int(offpeak) if win.contains(now_utc.time()) else int(daytime)
