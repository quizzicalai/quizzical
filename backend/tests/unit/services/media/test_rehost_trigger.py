"""§21 Phase 5 — rehost trigger (`AC-PRECOMP-IMG-2`)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.services.media.rehost import needs_rehost


def test_expiring_asset_is_queued_for_rehost():
    now = datetime(2030, 1, 1, tzinfo=UTC)
    soon = now + timedelta(days=3)  # within 7-day window
    assert needs_rehost(expires_at=soon, now=now, window_days=7) is True


def test_distant_asset_is_not_queued():
    now = datetime(2030, 1, 1, tzinfo=UTC)
    later = now + timedelta(days=30)
    assert needs_rehost(expires_at=later, now=now, window_days=7) is False


def test_already_expired_qualifies():
    now = datetime(2030, 1, 1, tzinfo=UTC)
    past = now - timedelta(days=1)
    assert needs_rehost(expires_at=past, now=now, window_days=7) is True


def test_null_expiry_never_qualifies():
    assert needs_rehost(expires_at=None, now=datetime.now(UTC), window_days=7) is False


def test_naive_datetime_treated_as_utc():
    now = datetime(2030, 1, 1, tzinfo=UTC)
    naive = datetime(2030, 1, 5)  # 4 days later, no tz
    assert needs_rehost(expires_at=naive, now=now, window_days=7) is True


def test_negative_window_rejected():
    with pytest.raises(ValueError):
        needs_rehost(expires_at=datetime.now(UTC), window_days=-1)


def test_zero_window_only_qualifies_already_expired():
    now = datetime(2030, 1, 1, tzinfo=UTC)
    assert needs_rehost(expires_at=now, now=now, window_days=0) is True
    assert needs_rehost(
        expires_at=now + timedelta(seconds=1), now=now, window_days=0
    ) is False
