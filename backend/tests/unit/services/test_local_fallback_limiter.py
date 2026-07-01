"""Process-local fallback cost limiter (Hitlist #3, 2026-06-30).

A coarse per-replica in-memory start cap that engages ONLY while Redis is
unreachable, so a sustained Redis outage cannot remove every $ ceiling. It is a
DEGRADE (not fail-closed): a brief blip still admits real users; the cap
evaporates the moment Redis recovers (the caller stops consulting it).
"""
from __future__ import annotations

import pytest

from app.services import local_fallback_limiter as lfl


@pytest.fixture(autouse=True)
def _clean_window():
    lfl.reset()
    yield
    lfl.reset()


@pytest.fixture
def _small_cap(monkeypatch):
    cfg = lfl.settings.security.live_cost_guard
    monkeypatch.setattr(cfg, "redis_outage_local_start_cap", 3, raising=False)
    monkeypatch.setattr(cfg, "redis_outage_local_window_s", 60, raising=False)
    return cfg


def test_allows_up_to_cap_then_blocks(_small_cap):
    # Use a fixed clock so the window never advances within the test.
    now = 1000.0
    assert lfl.allow_start(now=now) is True
    assert lfl.allow_start(now=now) is True
    assert lfl.allow_start(now=now) is True
    # 4th within the same window -> blocked.
    assert lfl.allow_start(now=now) is False


def test_window_slides_and_recovers(_small_cap):
    base = 1000.0
    for _ in range(3):
        assert lfl.allow_start(now=base) is True
    assert lfl.allow_start(now=base) is False  # cap hit
    # Advance past the 60s window: the old timestamps fall out, capacity returns.
    later = base + 61.0
    assert lfl.allow_start(now=later) is True
    assert lfl.allow_start(now=later) is True


def test_recovers_when_redis_returns(_small_cap):
    """Once Redis is back the caller stops consulting this limiter, but even if
    it were called, a fresh window admits again — the cap does not 'stick'."""
    now = 2000.0
    for _ in range(3):
        lfl.allow_start(now=now)
    assert lfl.allow_start(now=now) is False
    lfl.reset()  # models the caller no longer routing here (Redis healthy)
    assert lfl.allow_start(now=now) is True


def test_defaults_used_when_config_missing(monkeypatch):
    # Force the cfg lookup to blow up -> safe defaults (cap=60), never raises.
    monkeypatch.setattr(lfl, "_cfg", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    # allow_start swallows the error and fails OPEN.
    assert lfl.allow_start(now=1.0) is True


def test_nonpositive_config_falls_back_to_defaults(monkeypatch):
    cfg = lfl.settings.security.live_cost_guard
    monkeypatch.setattr(cfg, "redis_outage_local_start_cap", 0, raising=False)
    monkeypatch.setattr(cfg, "redis_outage_local_window_s", -5, raising=False)
    cap, window = lfl._cfg()
    assert cap == lfl._DEFAULT_CAP
    assert window == lfl._DEFAULT_WINDOW_S
