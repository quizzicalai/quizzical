# backend/app/api/endpoints/config.py
from __future__ import annotations

import os
from typing import Any, Dict

import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import settings

router = APIRouter()
logger = structlog.get_logger(__name__)


@router.get("/config")
def get_app_config() -> JSONResponse:
    """
    Return the frontend-facing configuration as a plain JSON response.
    This avoids any Pydantic serialization issues and gives us full control.
    """
    
    # Build the exact structure the frontend expects
    config = {
        "theme": {
            "colors": {
                "primary": "79 70 229",
                "secondary": "30 41 59",
                "accent": "234 179 8",
                "bg": "248 250 252",
                "fg": "15 23 42",
                "border": "226 232 240",
                "muted": "100 116 139",
                "ring": "129 140 248",
            },
            "fonts": {
                "sans": "Inter, sans-serif",
                "serif": "serif",
            },
            "dark": {
                "colors": {
                    "primary": "129 140 248",
                    "secondary": "226 232 240",
                    "accent": "250 204 21",
                    "bg": "15 23 42",
                    "fg": "248 250 252",
                    "border": "30 41 59",
                    "muted": "148 163 184",
                    "ring": "129 140 248",
                }
            }
        },
        "content": {
            "appName": "Quizzical",
            "landingPage": {},
            "footer": {
                "about": {"label": "About", "href": "/about"},
                "terms": {"label": "Terms", "href": "/terms"},
                "privacy": {"label": "Privacy", "href": "/privacy"},
                "donate": {"label": "Donate", "href": "#"},
            },
            "aboutPage": {
                "title": "About",
                "blocks": []
            },
            "termsPage": {
                "title": "Terms",
                "blocks": []
            },
            "privacyPolicyPage": {
                "title": "Privacy",
                "blocks": []
            },
            "errors": {
                "title": "Error",
                "retry": "Retry",
                "home": "Home",
                "startOver": "Start Over",
            }
        },
        "limits": {
            "validation": {
                "category_min_length": 3,
                "category_max_length": 100,
            }
        },
        "apiTimeouts": {
            "default": 15000,
            "startQuiz": 60000,
            "poll": {
                "total": 60000,
                "interval": 1000,
                "maxInterval": 5000,
            }
        },
        "features": {
            "turnstileEnabled": False,
            "turnstileSiteKey": os.getenv("TURNSTILE_SITE_KEY", ""),
        }
    }
    
    logger.info(
        "Frontend config served",
        config_keys=list(config.keys()),
        theme_keys=list(config["theme"].keys()),
        content_keys=list(config["content"].keys()),
    )
    
    return JSONResponse(content=config)