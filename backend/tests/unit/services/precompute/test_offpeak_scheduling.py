"""§21 Phase 7 — off-peak scheduling (`AC-PRECOMP-COST-5`)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.services.precompute.scheduling import current_concurrency, parse_window


def test_concurrency_switches_inside_window():
    inside = datetime(2026, 4, 30, 3, 30, tzinfo=UTC)  # 03:30 UTC
    outside = datetime(2026, 4, 30, 12, 0, tzinfo=UTC)  # noon UTC
    assert current_concurrency(
        inside, daytime=1, offpeak=4, window="02:00-08:00"
    ) == 4
    assert current_concurrency(
        outside, daytime=1, offpeak=4, window="02:00-08:00"
    ) == 1


def test_window_wraps_midnight():
    win = parse_window("22:00-04:00")
    assert win.contains(datetime(2026, 1, 1, 23, 30, tzinfo=UTC).time())
    assert win.contains(datetime(2026, 1, 1, 1, 0, tzinfo=UTC).time())
    assert not win.contains(datetime(2026, 1, 1, 6, 0, tzinfo=UTC).time())


def test_invalid_window_raises():
    with pytest.raises(ValueError):
        parse_window("not-a-window")


def test_window_boundaries_inclusive_start_exclusive_end():
    win = parse_window("02:00-08:00")
    assert win.contains(datetime(2026, 1, 1, 2, 0, tzinfo=UTC).time())
    assert not win.contains(datetime(2026, 1, 1, 8, 0, tzinfo=UTC).time())
