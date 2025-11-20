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

from app.main import app as fastapi_app
from app.api.dependencies import verify_turnstile


@pytest.fixture
def turnstile_bypass():
    """
    Bypass Turnstile verification using dependency overrides.
    """
    # Safe signature that asks for nothing
    async def _ok() -> bool:
        return True

    fastapi_app.dependency_overrides[verify_turnstile] = _ok
    try:
        yield
    finally:
        fastapi_app.dependency_overrides.pop(verify_turnstile, None)