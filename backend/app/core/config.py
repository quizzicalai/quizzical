from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr

# This makes the path to the .env file absolute from this file's location.
# It now correctly points to the project root, one level above the 'backend' directory.
# Assumes config.py is in backend/app/core/
ENV_PATH = Path(__file__).parent.parent.parent.parent / '.env'

class Settings(BaseSettings):
    # Use SecretStr to prevent leaking secrets in logs
    OPENAI_API_KEY: SecretStr = "sk-placeholder"
    # Ensure the driver is asyncpg for SQLAlchemy async support
    DATABASE_URL: str = "postgresql+asyncpg://user:pass@postgres:5432/quizdb"
    REDIS_URL: str = "redis://redis:6379"

    # Configuration for loading from a .env file.
    # Pydantic first checks for actual environment variables (like those from
    # docker-compose), then loads this file if they aren't found.
    model_config = SettingsConfigDict(
        env_file=ENV_PATH,
        env_file_encoding='utf-8',
        extra='ignore'
    )

# Create a singleton instance
settings = Settings()
