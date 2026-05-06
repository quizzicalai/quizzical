"""
§R16 — production Turnstile rollout via /api/config and env overrides.

The frontend reads features.turnstile and features.turnstileSiteKey from
GET /config. Production flips Turnstile on by setting:
  - ENABLE_TURNSTILE=true
  - TURNSTILE_SITE_KEY=<public site key>
on the Container App env (see infrastructure/scripts/sync-nonsecret-env-dev.sh).

These tests assert that env overrides win over YAML defaults so that:
- A fresh container deployment with the right env vars publishes the correct
  feature flags to the FE.
- An empty or missing TURNSTILE_SITE_KEY does not poison the response.

Acceptance criteria:
- AC-PROD-R16-TURNSTILE-1: ENABLE_TURNSTILE=true env → features.turnstile=true.
- AC-PROD-R16-TURNSTILE-2: ENABLE_TURNSTILE=false env → features.turnstile=false
  even if YAML says otherwise.
- AC-PROD-R16-TURNSTILE-3: TURNSTILE_SITE_KEY env → features.turnstileSiteKey
  exposed verbatim.
- AC-PROD-R16-TURNSTILE-4: features.turnstile and features.turnstileEnabled
  stay mirrored (legacy FE consumers).
"""
from __future__ import annotations

import pytest

from app.api.endpoints import config as config_module


@pytest.fixture
def _empty_yaml(monkeypatch):
    """Force the YAML loader to return an empty doc so env vars dominate."""
    monkeypatch.setattr(config_module, "_YAML", {}, raising=False)
    yield


def _build(monkeypatch, *, enable: str | None, site_key: str | None) -> dict:
    if enable is None:
        monkeypatch.delenv("ENABLE_TURNSTILE", raising=False)
    else:
        monkeypatch.setenv("ENABLE_TURNSTILE", enable)
    if site_key is None:
        monkeypatch.delenv("TURNSTILE_SITE_KEY", raising=False)
    else:
        monkeypatch.setenv("TURNSTILE_SITE_KEY", site_key)
    return config_module._frontend_config_from_yaml()


def test_enable_turnstile_true_env_flips_feature_on(_empty_yaml, monkeypatch):
    cfg = _build(monkeypatch, enable="true", site_key="0xPROD_SITE_KEY")
    features = cfg["features"]
    assert features["turnstile"] is True
    assert features["turnstileEnabled"] is True
    assert features["turnstileSiteKey"] == "0xPROD_SITE_KEY"


def test_enable_turnstile_false_env_overrides_yaml(monkeypatch):
    # YAML says enabled, env says disabled — env must win.
    monkeypatch.setattr(
        config_module, "_YAML",
        {"quizzical": {"frontend": {"features": {"turnstile": True}}}},
        raising=False,
    )
    cfg = _build(monkeypatch, enable="false", site_key=None)
    features = cfg["features"]
    assert features["turnstile"] is False
    assert features["turnstileEnabled"] is False


def test_missing_site_key_does_not_break_response(_empty_yaml, monkeypatch):
    cfg = _build(monkeypatch, enable="true", site_key=None)
    features = cfg["features"]
    assert features["turnstile"] is True
    # No site key → key is absent from the payload (FE has its own VITE fallback).
    assert "turnstileSiteKey" not in features or features.get("turnstileSiteKey") in (
        None, "",
    )


def test_turnstile_and_legacy_alias_stay_mirrored(_empty_yaml, monkeypatch):
    cfg = _build(monkeypatch, enable="true", site_key="k")
    features = cfg["features"]
    assert features["turnstile"] == features["turnstileEnabled"]
