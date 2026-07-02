"""Recency window: only posts from the last reply window are considered."""
from datetime import datetime, timedelta, timezone

from social_agent.windowing import parse_x_timestamp, window_start, within_window

NOW = datetime(2026, 7, 2, 12, 0, 0, tzinfo=timezone.utc)


def test_inside_window():
    assert within_window(NOW - timedelta(hours=1), now=NOW)
    assert within_window(NOW - timedelta(hours=3, minutes=59), now=NOW)


def test_boundary_is_inclusive():
    assert within_window(NOW - timedelta(hours=4), now=NOW)


def test_outside_window():
    assert not within_window(NOW - timedelta(hours=4, seconds=1), now=NOW)
    assert not within_window(NOW - timedelta(days=1), now=NOW)


def test_future_timestamps_are_not_in_window():
    assert not within_window(NOW + timedelta(hours=1), now=NOW)


def test_small_clock_skew_tolerated():
    assert within_window(NOW + timedelta(seconds=60), now=NOW)


def test_x_api_string_timestamps():
    assert within_window("2026-07-02T11:30:00.000Z", now=NOW)
    assert not within_window("2026-07-02T01:00:00.000Z", now=NOW)


def test_parse_x_timestamp_handles_z_and_offset():
    a = parse_x_timestamp("2026-07-02T11:30:00.000Z")
    b = parse_x_timestamp("2026-07-02T11:30:00+00:00")
    assert a == b
    assert a.tzinfo is not None


def test_custom_window_hours():
    assert within_window(NOW - timedelta(hours=10), now=NOW, window_hours=12)
    assert not within_window(NOW - timedelta(hours=10), now=NOW, window_hours=4)


def test_window_start():
    assert window_start(now=NOW) == NOW - timedelta(hours=4)
