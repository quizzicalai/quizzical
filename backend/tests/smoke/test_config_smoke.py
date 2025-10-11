# tests/smoke/test_config_smoke.py

import sys
from pathlib import Path

# --- Ensure repo root (which contains "backend/") is on sys.path ---
_here = Path(__file__).resolve()
backend_dir = next((p for p in _here.parents if p.name == "backend"), None)
repo_root = backend_dir.parent if backend_dir else _here.parents[3]  # fallback for CI layouts
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

import importlib
import asyncio
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


@pytest.mark.unit
def test_wikipedia_search_respects_retrieval_policy(monkeypatch):
    """
    With retrieval policy disallowing Wikipedia, the tool should short-circuit
    and return an empty string (no network calls).
    """
    dt = importlib.import_module("backend.app.agent.tools.data_tools")

    class _DummySettings:
        class _Retrieval:
            policy = "off"
            allow_wikipedia = False
            allow_web = False
            max_calls_per_run = 0
            allowed_domains = []
        retrieval = _Retrieval()
        llm_tools = {}  # not needed when retrieval is blocked

    # Replace the module-level settings with our dummy that forbids retrieval
    monkeypatch.setattr(dt, "settings", _DummySettings(), raising=False)

    out = dt.wikipedia_search("Captain Picard")
    assert out == ""


@pytest.mark.unit
def test_web_search_respects_retrieval_policy(monkeypatch):
    """
    With retrieval policy disallowing web search, the tool should short-circuit
    and return an empty string (no SDK usage or network).
    """
    dt = importlib.import_module("backend.app.agent.tools.data_tools")

    class _DummySettings:
        class _Retrieval:
            policy = "off"
            allow_wikipedia = False
            allow_web = False
            max_calls_per_run = 0
            allowed_domains = []
        retrieval = _Retrieval()
        llm_tools = {}  # not needed when retrieval is blocked

    monkeypatch.setattr(dt, "settings", _DummySettings(), raising=False)

    # IMPORTANT: call the tool asynchronously via .ainvoke(...)
    result = asyncio.run(dt.web_search.ainvoke({"query": "test query"}))
    assert result == ""