from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

import structlog
import yaml
from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()
logger = structlog.get_logger(__name__)

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


def _bool_from_env(name: str) -> Optional[bool]:
    """Return bool if env is set, else None. Accepts true/false/1/0/yes/no."""
    raw = os.getenv(name)
    if raw is None:
        return None
    val = raw.strip().lower()
    if val in {"1", "true", "yes", "y", "on"}:
        return True
    if val in {"0", "false", "no", "n", "off"}:
        return False
    return None


_YAML = _load_yaml_config(APP_CONFIG_PATH)


def _frontend_config_from_yaml() -> Dict[str, Any]:
    """
    Build the frontend-facing config and AUTHORITATIVELY expose:
      - features.turnstile (boolean)  ← this is the flag FE uses
      - features.turnstileEnabled     ← legacy mirror kept in sync
      - features.turnstileSiteKey     ← optional, env overrides YAML
    """
    q = (_YAML.get("quizzical") or {})
    frontend = (q.get("frontend") or {})
    limits = (frontend.get("limits") or q.get("limits") or {})
    api_timeouts = (frontend.get("apiTimeouts") or q.get("apiTimeouts") or {})
    features_in = (frontend.get("features") or q.get("features") or {})

    # Decide the boolean from (in order): ENV override → YAML 'turnstile' → YAML 'turnstileEnabled' → default True
    env_turnstile = _bool_from_env("ENABLE_TURNSTILE")
    yaml_turnstile = features_in.get("turnstile")
    yaml_enabled = features_in.get("turnstileEnabled")

    if isinstance(yaml_turnstile, bool):
        yaml_bool = yaml_turnstile
    elif isinstance(yaml_enabled, bool):
        yaml_bool = yaml_enabled
    else:
        yaml_bool = None

    turnstile_bool = env_turnstile if env_turnstile is not None else (yaml_bool if yaml_bool is not None else True)

    # Optional site key override via env
    site_key_env = os.getenv("TURNSTILE_SITE_KEY")
    site_key_yaml = features_in.get("turnstileSiteKey")
    site_key = site_key_env if site_key_env else site_key_yaml

    features_out: Dict[str, Any] = {
        **features_in,
        "turnstile": turnstile_bool,
        "turnstileEnabled": turnstile_bool,  # keep legacy consumer(s) aligned
    }
    if site_key is not None:
        features_out["turnstileSiteKey"] = site_key

    out: Dict[str, Any] = {
        "theme": frontend.get("theme", {}),
        "content": frontend.get("content", {}),
        "limits": limits,
        "apiTimeouts": api_timeouts,
        "features": features_out,
    }

    # Strip Nones
    return {k: v for k, v in out.items() if v is not None} # type: ignore[func-returns-value]


@router.get("/config")
def get_app_config() -> JSONResponse:
    config = _frontend_config_from_yaml()

    logger.info(
        "Frontend config served",
        features_keys=list(config.get("features", {}).keys()),
        turnstile=config.get("features", {}).get("turnstile"),
    )

    return JSONResponse(content=config)
