"""§17.4 — Server-Timing per-segment breakdown (AC-SCALE-TIMING-*)."""

from __future__ import annotations

import pytest


def test_timing_recorder_serializes_named_segments() -> None:
    """AC-SCALE-TIMING-1: Server-Timing string includes every recorded segment."""
    from app.core.server_timing import TimingRecorder

    rec = TimingRecorder()
    rec.record("db", 12.7)
    rec.record("redis", 3.1)
    rec.record("llm", 18.0)
    header = rec.to_header(app_dur_ms=42.3)
    parts = [p.strip() for p in header.split(",")]
    assert parts[0] == "app;dur=42.3"
    names = {p.split(";")[0] for p in parts}
    assert {"app", "db", "redis", "llm"} <= names


def test_timing_recorder_drops_invalid_names() -> None:
    """AC-SCALE-TIMING-2: invalid names silently dropped, no header injection."""
    from app.core.server_timing import TimingRecorder

    rec = TimingRecorder()
    rec.record("ok_name", 1.0)
    rec.record("bad name", 1.0)  # space invalid
    rec.record("bad,name", 1.0)
    rec.record("bad;name", 1.0)
    rec.record("with-dash", 1.0)
    rec.record("with_under", 1.0)
    rec.record("123-num", 1.0)
    rec.record("", 1.0)
    rec.record("x" * 200, 1.0)  # too long, should drop

    header = rec.to_header(app_dur_ms=10.0)
    assert ";" in header  # well-formed
    # Make sure invalid characters never appear as segment names.
    bad_segments = ["bad name", "bad,name", "bad;name"]
    for bad in bad_segments:
        assert f"{bad};dur=" not in header
    # Valid names present.
    assert "ok_name;dur=" in header
    assert "with-dash;dur=" in header
    assert "with_under;dur=" in header
    assert "123-num;dur=" in header


def test_timing_recorder_accumulates_duplicates() -> None:
    """AC-SCALE-TIMING-3: duplicate segment names sum, insertion order preserved."""
    from app.core.server_timing import TimingRecorder

    rec = TimingRecorder()
    rec.record("db", 5.0)
    rec.record("redis", 2.0)
    rec.record("db", 7.0)
    header = rec.to_header(app_dur_ms=1.0)
    parts = [p.strip() for p in header.split(",")]
    assert parts[0] == "app;dur=1.0"
    # db should appear before redis, and accumulate to 12.0
    assert parts[1].startswith("db;dur=")
    assert "12" in parts[1]  # 12.0 (rounding-tolerant)
    assert parts[2].startswith("redis;dur=")


def test_timing_recorder_only_app_when_empty() -> None:
    """AC-SCALE-TIMING-4: empty recorder → header reduces to just ``app;dur=…``."""
    from app.core.server_timing import TimingRecorder

    rec = TimingRecorder()
    header = rec.to_header(app_dur_ms=12.5)
    assert header == "app;dur=12.5"


def test_negative_durations_are_clamped_to_zero() -> None:
    """Defensive: negative duration → 0.0, never propagated."""
    from app.core.server_timing import TimingRecorder

    rec = TimingRecorder()
    rec.record("db", -5.0)
    header = rec.to_header(app_dur_ms=1.0)
    assert "db;dur=0" in header


def test_request_carries_timing_recorder() -> None:
    """Middleware places a TimingRecorder on request.state for endpoint use."""
    from app.core.server_timing import get_request_timing
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/x",
        "headers": [],
        "raw_path": b"/x",
        "query_string": b"",
        "state": {},
    }
    request = Request(scope)
    rec = get_request_timing(request)
    assert rec is not None
    rec.record("redis", 1.0)
    rec2 = get_request_timing(request)
    assert rec is rec2
