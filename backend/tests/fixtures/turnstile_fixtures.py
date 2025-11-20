# backend/tests/fixtures/turnstile_fixtures.py
"""
Turnstile fixtures.

These fixtures ensure that /quiz endpoints don't fail due to Turnstile during tests.
We disable the feature flag *and* stub the verifier so the code path remains stable
regardless of settings.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure `backend/` is importable
_THIS_FILE = Path(__file__).resolve()
_BACKEND_DIR = _THIS_FILE.parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.core.config import settings
from app.api import dependencies as deps


@pytest.fixture
def turnstile_bypass(monkeypatch):
    """
    Bypass Turnstile verification in unit/integration tests.

    1. Force settings.security.enabled to False so dependencies.verify_turnstile short-circuits.
    2. Stub dependencies.verify_turnstile to always succeed in case code calls it anyway.
    """
    # 1. Disable via config (Pydantic model attribute override)
    try:
        # settings.security is a Pydantic model instance; monkeypatch works on attributes
        monkeypatch.setattr(settings.security, "enabled", False, raising=False)
    except Exception:
        pass

    # 2. Stub the dependency function directly
    async def _ok(*args, **kwargs) -> bool:
        return True

    monkeypatch.setattr(deps, "verify_turnstile", _ok, raising=True)
    yield