# backend/app/api/config.py
from fastapi import APIRouter
from pydantic import BaseModel
from app.core.config import settings

router = APIRouter()

# ---- Add a DTO that mirrors the frontend's AppConfig shape ----
class ApiTimeouts(BaseModel):
    default: int = 15_000
    startQuiz: int = 60_000
    poll: dict = {"total": 60_000, "interval": 1_000, "maxInterval": 5_000}

class FrontendAppConfig(BaseModel):
    theme: dict
    content: dict
    limits: dict
    apiTimeouts: ApiTimeouts

@router.get("/config", response_model=FrontendAppConfig)
def get_app_config():
    """
    Return only the frontend-facing configuration in the shape the React app expects.
    """
    # Source from settings.frontend, but move them to top-level
    theme = settings.frontend.theme.model_dump()
    content = settings.frontend.content.model_dump()

    # Provide the validation limits the UI needs (backend doesn't define these today)
    limits = {
        "validation": {
            "category_min_length": 3,
            "category_max_length": 100,
        }
    }

    # Provide reasonable API timeout defaults expected by the UI
    api_timeouts = ApiTimeouts().model_dump()

    return {
        "theme": theme,
        "content": content,
        "limits": limits,
        "apiTimeouts": api_timeouts,
    }
