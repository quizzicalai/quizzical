# backend/app/api/config.py
from __future__ import annotations

import os
from typing import Any, Dict

import structlog
from fastapi import APIRouter
from pydantic import BaseModel

from app.core.config import settings

router = APIRouter()
logger = structlog.get_logger(__name__)


# ------------------------------
# Response DTOs (frontend shape)
# ------------------------------

class ApiTimeouts(BaseModel):
    default: int = 15_000
    startQuiz: int = 60_000
    poll: Dict[str, int] = {"total": 60_000, "interval": 1_000, "maxInterval": 5_000}


class Features(BaseModel):
    turnstileEnabled: bool = False
    turnstileSiteKey: str = ""


class FrontendAppConfig(BaseModel):
    theme: Dict[str, Any]
    content: Dict[str, Any]
    limits: Dict[str, Any]
    apiTimeouts: ApiTimeouts
    features: Features


def _safe_settings_dict(path: str, default: Dict[str, Any]) -> Dict[str, Any]:
    """
    Safely read a nested dict off `settings` via dotted path (e.g., "frontend.theme"),
    returning `default` if any attribute is missing or not a pydantic model/dict.
    """
    try:
        cur: Any = settings
        for part in path.split("."):
            cur = getattr(cur, part)
        if hasattr(cur, "model_dump"):
            return cur.model_dump()
        if isinstance(cur, dict):
            return cur
        # Anything else -> fallback
        return default
    except Exception:
        return default


@router.get("/config", response_model=FrontendAppConfig)
def get_app_config() -> FrontendAppConfig:
    """
    Return only the frontend-facing configuration in the shape the React app expects.

    This endpoint is defensive:
      - If `settings.frontend.theme/content` are missing, it falls back to sane defaults.
      - `features` are included in the response model to avoid validation mismatches.
    """
    # Safe defaults if not present in settings
    theme = _safe_settings_dict(
        "frontend.theme",
        {
            "mode": "light",
            "primaryColor": "#6E56CF",
            "secondaryColor": "#0EA5E9",
            "logoUrl": "",
        },
    )
    content = _safe_settings_dict(
        "frontend.content",
        {
            "appName": "Quizzical",
            "tagline": "Quick, fun, and surprisingly accurate quizzes.",
            "startCta": "Start your quiz",
            "proceedCta": "Proceed",
            "tryAnotherCta": "Try another topic",
        },
    )

    # Limits the UI expects (kept local to the API)
    limits = {
        "validation": {
            "category_min_length": 3,
            "category_max_length": 100,
        }
    }

    # Reasonable defaults; adjust if you later surface these in YAML
    api_timeouts = ApiTimeouts()

    # Features (turnstile is safe to expose by site key only)
    turnstile_enabled = getattr(settings, "ENABLE_TURNSTILE", False)
    turnstile_site_key = os.getenv("TURNSTILE_SITE_KEY", "")  # site key is public

    features = Features(
        turnstileEnabled=bool(turnstile_enabled),
        turnstileSiteKey=turnstile_site_key,
    )

    logger.debug(
        "Frontend config served",
        has_theme=bool(theme),
        has_content=bool(content),
        turnstileEnabled=features.turnstileEnabled,
        siteKey_present=bool(features.turnstileSiteKey),
    )

    return FrontendAppConfig(
        theme=theme,
        content=content,
        limits=limits,
        apiTimeouts=api_timeouts,
        features=features,
    )
