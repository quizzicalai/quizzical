import os
from pathlib import Path
from typing import Any, Dict
from unittest.mock import patch

import pytest
import yaml
from fastapi.testclient import TestClient

# Module under test
from app.api.endpoints import config as config_mod

# ---------------------------------------------------------------------
# Tests for Helper Functions
# ---------------------------------------------------------------------

def test_bool_from_env(monkeypatch):
    # Truthy
    monkeypatch.setenv("TEST_BOOL", "1")
    assert config_mod._bool_from_env("TEST_BOOL") is True
    monkeypatch.setenv("TEST_BOOL", "true")
    assert config_mod._bool_from_env("TEST_BOOL") is True
    monkeypatch.setenv("TEST_BOOL", "ON")
    assert config_mod._bool_from_env("TEST_BOOL") is True

    # Falsy
    monkeypatch.setenv("TEST_BOOL", "0")
    assert config_mod._bool_from_env("TEST_BOOL") is False
    monkeypatch.setenv("TEST_BOOL", "False")
    assert config_mod._bool_from_env("TEST_BOOL") is False
    monkeypatch.setenv("TEST_BOOL", "off")
    assert config_mod._bool_from_env("TEST_BOOL") is False

    # Missing or Unknown
    monkeypatch.delenv("TEST_BOOL", raising=False)
    assert config_mod._bool_from_env("TEST_BOOL") is None
    
    monkeypatch.setenv("TEST_BOOL", "random")
    assert config_mod._bool_from_env("TEST_BOOL") is None

def test_load_yaml_config(tmp_path):
    # 1. Valid File
    f = tmp_path / "test.yaml"
    f.write_text("key: value", encoding="utf-8")
    data = config_mod._load_yaml_config(f)
    assert data == {"key": "value"}

    # 2. Missing File
    assert config_mod._load_yaml_config(tmp_path / "missing.yaml") == {}

    # 3. Invalid YAML (parsing error)
    bad = tmp_path / "bad.yaml"
    bad.write_text("key: [unclosed", encoding="utf-8")
    # Should log error and return {}
    assert config_mod._load_yaml_config(bad) == {}

    # 4. Non-dict YAML (e.g. list)
    lst = tmp_path / "list.yaml"
    lst.write_text("- item", encoding="utf-8")
    assert config_mod._load_yaml_config(lst) == {}

# ---------------------------------------------------------------------
# Tests for Logic: _frontend_config_from_yaml
# ---------------------------------------------------------------------

@pytest.fixture
def mock_yaml(monkeypatch):
    """Helper to set the module-level _YAML variable."""
    def _set(data: Dict[str, Any]):
        monkeypatch.setattr(config_mod, "_YAML", data)
    return _set

def test_config_logic_defaults(mock_yaml, monkeypatch):
    """Ensure defaults when YAML is empty and ENV is unset."""
    mock_yaml({})
    monkeypatch.delenv("ENABLE_TURNSTILE", raising=False)
    
    cfg = config_mod._frontend_config_from_yaml()
    
    # Defaults
    assert cfg["features"]["turnstile"] is True
    assert cfg["features"]["turnstileEnabled"] is True

def test_config_logic_yaml_precedence(mock_yaml, monkeypatch):
    """YAML 'turnstile' > 'turnstileEnabled'."""
    monkeypatch.delenv("ENABLE_TURNSTILE", raising=False)
    
    # Scenario: turnstile=False, turnstileEnabled=True -> False wins
    data = {
        "quizzical": {
            "frontend": {
                "features": {
                    "turnstile": False,
                    "turnstileEnabled": True
                }
            }
        }
    }
    mock_yaml(data)
    cfg = config_mod._frontend_config_from_yaml()
    assert cfg["features"]["turnstile"] is False

def test_config_logic_legacy_yaml(mock_yaml, monkeypatch):
    """Fallback to 'turnstileEnabled' if 'turnstile' missing."""
    monkeypatch.delenv("ENABLE_TURNSTILE", raising=False)
    
    data = {
        "quizzical": {
            "frontend": {
                "features": {
                    "turnstileEnabled": False
                }
            }
        }
    }
    mock_yaml(data)
    cfg = config_mod._frontend_config_from_yaml()
    assert cfg["features"]["turnstile"] is False

def test_config_logic_env_override(mock_yaml, monkeypatch):
    """ENV overrides everything."""
    # YAML says True
    mock_yaml({"quizzical": {"frontend": {"features": {"turnstile": True}}}})
    
    # ENV says False
    monkeypatch.setenv("ENABLE_TURNSTILE", "false")
    
    cfg = config_mod._frontend_config_from_yaml()
    assert cfg["features"]["turnstile"] is False

def test_site_key_override(mock_yaml, monkeypatch):
    """Environment variable overrides site key."""
    mock_yaml({"quizzical": {"frontend": {"features": {"turnstileSiteKey": "yaml-key"}}}})
    
    # 1. No ENV -> YAML key
    monkeypatch.delenv("TURNSTILE_SITE_KEY", raising=False)
    cfg = config_mod._frontend_config_from_yaml()
    assert cfg["features"]["turnstileSiteKey"] == "yaml-key"
    
    # 2. With ENV -> ENV key
    monkeypatch.setenv("TURNSTILE_SITE_KEY", "env-key")
    cfg2 = config_mod._frontend_config_from_yaml()
    assert cfg2["features"]["turnstileSiteKey"] == "env-key"

# ---------------------------------------------------------------------
# API Endpoint Tests
# ---------------------------------------------------------------------

@pytest.mark.anyio
async def test_get_config_endpoint(async_client):
    response = await async_client.get("/api/v1/config")
    assert response.status_code == 200
    data = response.json()
    
    assert "features" in data
    assert "limits" in data
    assert "turnstile" in data["features"]