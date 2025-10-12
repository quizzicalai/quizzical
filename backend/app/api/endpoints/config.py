from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import structlog
import yaml
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()
logger = structlog.get_logger(__name__)

# Path to the YAML. Keep flexible so other environments can override.
# Defaults to the same file your backend already uses.
APP_CONFIG_PATH = os.getenv("APP_CONFIG_PATH", "appconfig.local.yaml")


def _load_yaml_config(path: str | os.PathLike) -> Dict[str, Any]:
  p = Path(path)
  if not p.exists():
    logger.error("App config YAML not found", path=str(p))
    return {}
  try:
    with p.open("r", encoding="utf-8") as f:
      data = yaml.safe_load(f) or {}
      return data if isinstance(data, dict) else {}
  except Exception:
    logger.exception("Failed to load YAML app config", path=str(p))
    return {}


# Load once at import (same as your prior in-memory defaults).
_YAML = _load_yaml_config(APP_CONFIG_PATH)


def _frontend_config_from_yaml() -> Dict[str, Any]:
  """
  Extract the frontend-facing configuration from YAML:
    quizzical.frontend + limits + apiTimeouts + features

  Mirrors the shape the frontend expects today.
  """
  q = (_YAML.get("quizzical") or {})
  frontend = (q.get("frontend") or {})
  limits = (frontend.get("limits") or q.get("limits") or {})
  api_timeouts = (frontend.get("apiTimeouts") or q.get("apiTimeouts") or {})
  features = (frontend.get("features") or q.get("features") or {})

  # Optional secret/env overlay: allow env to override the site key without
  # baking defaults into code. This is NOT a default; it's an override.
  env_ts = os.getenv("TURNSTILE_SITE_KEY")
  if env_ts:
    features = {**features, "turnstileSiteKey": env_ts}

  # Return a dict that looks exactly like what the frontend receives today.
  out: Dict[str, Any] = {
    "theme": frontend.get("theme", {}),
    "content": frontend.get("content", {}),
    "limits": limits,
    "apiTimeouts": api_timeouts,
    # 'features' is optional in the FE; still include if present
    "features": features if features else frontend.get("features"),
  }
  # Remove None to avoid sending nulls
  return {k: v for k, v in out.items() if v is not None}


@router.get("/config")
def get_app_config() -> JSONResponse:
  """
  Return the frontend-facing configuration as plain JSON,
  sourced entirely from appconfig.local.yaml.
  """
  config = _frontend_config_from_yaml()

  logger.info(
    "Frontend config served (from YAML)",
    config_keys=list(config.keys()),
    theme_keys=list(config["theme"].keys()) if "theme" in config else [],
    content_keys=list(config["content"].keys()) if "content" in config else [],
  )

  return JSONResponse(content=config)
