"""Iter D — config endpoint: drop import-time YAML cache + add Cache-Control.

Two issues in ``app/api/endpoints/config.py``:

1. ``_YAML = _load_yaml_config(APP_CONFIG_PATH)`` runs at module
   **import time** and is never refreshed. This means:
     * Any change to the YAML on disk is invisible until the process is
       restarted.
     * Tests cannot easily inject a different config without
       re-importing the module.
   The fix is to call ``_load_yaml_config`` per-request. The file is
   tiny (<10 KB typical) and the path is local; the cost is negligible.

2. The README states the config route must send
   ``Cache-Control: public, max-age=...``. The current handler returns a
   plain ``JSONResponse`` with no ``Cache-Control`` header at all,
   forcing every FE refresh to re-hit the endpoint. Add a 60 s public
   cache header.
"""

from __future__ import annotations

import os
import textwrap

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def yaml_config_file(tmp_path, monkeypatch):
    """Write a deterministic YAML config and point APP_CONFIG_PATH at it.

    Returns a callable to rewrite the file mid-test.
    """
    cfg_path = tmp_path / "appconfig.test.yaml"

    def _write(turnstile: bool, site_key: str = "kf-test") -> None:
        cfg_path.write_text(
            textwrap.dedent(
                f"""
                quizzical:
                  frontend:
                    theme:
                      primary: "#000"
                    content:
                      title: "T"
                    limits:
                      maxQuestions: 10
                    apiTimeouts:
                      default: 5
                    features:
                      turnstile: {str(turnstile).lower()}
                      turnstileSiteKey: "{site_key}"
                """
            ).strip()
            + "\n",
            encoding="utf-8",
        )

    _write(turnstile=True)
    monkeypatch.setenv("APP_CONFIG_PATH", str(cfg_path))
    monkeypatch.delenv("ENABLE_TURNSTILE", raising=False)
    monkeypatch.delenv("TURNSTILE_SITE_KEY", raising=False)
    return _write


def _make_client() -> TestClient:
    """Reload the config module under the active env and mount it on FastAPI."""
    import importlib

    from app.api.endpoints import config as config_module

    importlib.reload(config_module)
    app = FastAPI()
    app.include_router(config_module.router, prefix="/api")
    return TestClient(app)


def test_config_route_sends_cache_control_header(yaml_config_file) -> None:
    yaml_config_file(turnstile=True)
    client = _make_client()

    resp = client.get("/api/config")
    assert resp.status_code == 200
    cc = resp.headers.get("cache-control", "")
    assert "public" in cc.lower(), f"missing 'public' in Cache-Control: {cc!r}"
    assert "max-age=" in cc.lower(), f"missing 'max-age=' in Cache-Control: {cc!r}"


def test_config_route_reflects_yaml_changes_without_restart(yaml_config_file) -> None:
    """Edit the YAML between two requests and verify the response updates.

    This currently fails because ``_YAML`` is captured at import time.
    """
    yaml_config_file(turnstile=True)
    client = _make_client()

    first = client.get("/api/config").json()
    assert first["features"]["turnstile"] is True

    # Rewrite the YAML on disk; no restart.
    yaml_config_file(turnstile=False, site_key="kf-rewritten")

    second = client.get("/api/config").json()
    assert second["features"]["turnstile"] is False, (
        "config endpoint kept stale YAML; should re-read on each request"
    )
    assert second["features"].get("turnstileSiteKey") == "kf-rewritten"


def test_config_module_has_no_import_time_yaml_cache() -> None:
    """``_YAML`` must not be a populated module-level constant. If kept for
    backward compat, it must be empty / lazy.
    """
    import importlib

    from app.api.endpoints import config as config_module

    importlib.reload(config_module)
    cached = getattr(config_module, "_YAML", None)
    assert cached in (None, {}), (
        f"config module still caches YAML at import time: {cached!r}"
    )


@pytest.fixture(autouse=True)
def _restore_config_module():
    """After each test reload, restore module to its on-disk import state."""
    yield
    import importlib

    from app.api.endpoints import config as config_module

    # Reset env so subsequent tests get clean state.
    for k in ("APP_CONFIG_PATH", "ENABLE_TURNSTILE", "TURNSTILE_SITE_KEY"):
        os.environ.pop(k, None)
    importlib.reload(config_module)
