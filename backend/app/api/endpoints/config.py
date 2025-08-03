from fastapi import APIRouter
from app.core.config import settings, Settings

router = APIRouter()

@router.get("/config", response_model=Settings)
def get_app_config():
    """
    Retrieve the entire application configuration object.
    This includes both backend and frontend settings.
    """
    return settings