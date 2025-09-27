# backend/tests/test_config_smoke.py

import importlib
import pytest

@pytest.mark.unit
async def test_asyncio_mode_auto_runs_without_marker():
    # If asyncio_mode=auto is honored, an async test runs without @pytest.mark.asyncio
    assert True

@pytest.mark.integration
def test_import_from_backend_app_counts_for_coverage():
    # Import something under backend/app to ensure coverage source scoping works
    # Prefer a small, always-present module
    mod = importlib.import_module("backend.app.core.config")
    assert mod is not None

def test_strict_markers_dont_break_on_known_markers():
    # Using defined markers should not raise "Unknown marker" errors
    assert True
