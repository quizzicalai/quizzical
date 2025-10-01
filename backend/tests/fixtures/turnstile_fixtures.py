# tests/fixtures/turnstile_fixtures.py

"""
Turnstile fixtures

These fixtures ensure that /quiz endpoints don't fail due to Turnstile during tests.
We disable the feature flag *and* stub the verifier so the code path remains stable
regardless of settings.
"""

import pytest

from app.core.config import settings
from app.api import dependencies as deps


@pytest.fixture
def turnstile_bypass(monkeypatch):
    """
    Bypass Turnstile verification in unit/integration tests.

    - Force security.enabled to False so dependencies.verify_turnstile short-circuits.
    - Also stub dependencies.verify_turnstile to always succeed in case code calls it anyway.
    """
    try:
        # This makes settings.ENABLE_TURNSTILE property resolve to False.
        monkeypatch.setattr(settings.security, "enabled", False, raising=False)
    except Exception:
        pass

    async def _ok(*_a, **_k):
        return True

    # Be defensive: even if the flag is ignored somewhere, this keeps it green.
    monkeypatch.setattr(deps, "verify_turnstile", _ok, raising=True)
    yield
