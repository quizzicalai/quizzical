# backend/tests/fixtures/env_fixtures.py

"""
Environment & settings fixtures for QuizzicalAI tests.

What this provides (opt-in):
- set_test_env: Pins safe environment variables for tests (session-scoped).
- reload_settings: Clears and rehydrates app settings after env changes.

Notes:
- We DO NOT use pytest's `monkeypatch` here for session-scoped env writes
  to avoid scope-mismatch with function-scoped `monkeypatch`.
- We update os.environ directly and restore initial values at the end of session.
- We also ensure `backend/` is on sys.path so `from app...` imports work in tests.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, Optional

import pytest


# Make sure `backend/` is importable: .../backend/tests/fixtures/env_fixtures.py -> parents[2] == backend/
_THIS_FILE = Path(__file__).resolve()
_BACKEND_DIR = _THIS_FILE.parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))


@pytest.fixture(scope="session")
def set_test_env() -> any:
    """
    Session-scoped fixture that sets stable defaults for tests.

    Defaults chosen to:
      - Keep the app in "local/dev" safety mode.
      - Force in-memory checkpointer in the agent graph (no Redis needed).
      - Bypass Turnstile in code paths that read settings.
      - Provide harmless defaults for cache and Redis URL (pool creation doesn't connect).

    Environment vars set (if not already set):
      APP_ENVIRONMENT=local
      USE_MEMORY_SAVER=1
      ENABLE_TURNSTILE=false
      OPENAI_API_KEY=test-key
      CACHE_NAMESPACE=test
      REDIS_URL=redis://localhost:6379/0
    """
    # Remember original values to restore later
    original: Dict[str, Optional[str]] = {
        "APP_ENVIRONMENT": os.getenv("APP_ENVIRONMENT"),
        "USE_MEMORY_SAVER": os.getenv("USE_MEMORY_SAVER"),
        "ENABLE_TURNSTILE": os.getenv("ENABLE_TURNSTILE"),
        "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY"),
        "CACHE_NAMESPACE": os.getenv("CACHE_NAMESPACE"),
        "REDIS_URL": os.getenv("REDIS_URL"),
    }

    # Set safe defaults (do not overwrite if already set by caller/CI)
    os.environ.setdefault("APP_ENVIRONMENT", "local")
    os.environ.setdefault("USE_MEMORY_SAVER", "1")
    os.environ.setdefault("ENABLE_TURNSTILE", "false")
    os.environ.setdefault("OPENAI_API_KEY", "test-key")
    os.environ.setdefault("CACHE_NAMESPACE", "test")
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

    try:
        yield
    finally:
        # Restore original env
        for k, v in original.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture()
def reload_settings(set_test_env):
    """
    Clear the LRU cache for settings and return a fresh settings object.

    Use this when changing env vars inside a test and you need `settings`
    to reflect those changes.

    Example:
        def test_toggle_security(reload_settings, monkeypatch):
            monkeypatch.setenv("ENABLE_TURNSTILE", "true")
            settings = reload_settings()
            assert settings.security.enabled is True
    """
    # Lazy import to avoid hard dependency if a test never touches settings
    from app.core.config import get_settings  # type: ignore

    # Clear and return a fresh model
    get_settings.cache_clear()
    return get_settings()
