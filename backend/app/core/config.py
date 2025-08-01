from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import SecretStr

class Settings(BaseSettings):
    # Use SecretStr to prevent leaking secrets in logs
    OPENAI_API_KEY: SecretStr = "sk-placeholder"
    # Ensure the driver is asyncpg for SQLAlchemy async support
    DATABASE_URL: str = "postgresql+asyncpg://user:pass@postgres:5432/quizdb"
    REDIS_URL: str = "redis://redis:6379"

    # Configuration for loading from a .env file (if used locally)
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

# Create a singleton instance
settings = Settings()