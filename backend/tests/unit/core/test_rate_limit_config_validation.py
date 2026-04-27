"""§9.7.3 — RateLimitConfig validators (AC-RL-CONFIG-1..5).

Misconfiguration must fail loudly at startup. A typo cannot silently
disable rate limiting or lock the app out of itself.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import RateLimitConfig


class TestRateLimitConfigValidators:
    def test_default_validates_cleanly(self):
        # AC-RL-CONFIG-5
        cfg = RateLimitConfig()
        assert cfg.capacity == 30
        assert cfg.refill_per_second == 1.0

    def test_capacity_zero_rejected(self):
        # AC-RL-CONFIG-1
        with pytest.raises(ValidationError):
            RateLimitConfig(capacity=0)

    def test_capacity_negative_rejected(self):
        # AC-RL-CONFIG-2
        with pytest.raises(ValidationError):
            RateLimitConfig(capacity=-5)

    def test_refill_zero_rejected(self):
        # AC-RL-CONFIG-3
        with pytest.raises(ValidationError):
            RateLimitConfig(refill_per_second=0)

    def test_refill_negative_rejected(self):
        # AC-RL-CONFIG-4
        with pytest.raises(ValidationError):
            RateLimitConfig(refill_per_second=-1.0)

    def test_valid_overrides_accepted(self):
        cfg = RateLimitConfig(capacity=10, refill_per_second=2.0)
        assert cfg.capacity == 10
        assert cfg.refill_per_second == 2.0
