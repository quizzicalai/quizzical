# backend/app/core/config.py
import os
from functools import lru_cache

from azure.appconfiguration import AzureAppConfigurationClient
from azure.identity import DefaultAzureCredential
from pydantic import BaseModel, PostgresDsn, RedisDsn, computed_field
from pydantic_settings import BaseSettings

# =============================================================================
# Pydantic Models for Configuration Structure
# =============================================================================
class ProjectSettings(BaseModel):
    name: str
    api_prefix: str

class AgentSettings(BaseModel):
    max_retries: int

class LLMParams(BaseModel):
    temperature: float
    top_p: float | None = None

class LLMToolSetting(BaseModel):
    model_name: str
    default_params: LLMParams

class LLMPromptSetting(BaseModel):
    system_prompt: str
    user_prompt_template: str
    
class DatabaseSettings(BaseModel):
    host: str
    port: int
    user: str
    db_name: str

class RedisSettings(BaseModel):
    host: str
    port: int
    db: int

class FrontendThemeColors(BaseModel):
    primary: str
    secondary: str
    accent: str
    muted: str
    background: str
    white: str

class FrontendTheme(BaseModel):
    colors: FrontendThemeColors
    fonts: dict[str, str]

class FrontendContent(BaseModel):
    brand: dict[str, str]
    footer: dict[str, str | list]
    landingPage: dict[str, str]
    finalPage: dict[str, str]
    notFoundPage: dict[str, str]
    notifications: dict[str, str]
    loadingStates: dict[str, str]
    errorStates: dict[str, str]

class FrontendSettings(BaseModel):
    theme: FrontendTheme
    content: FrontendContent


# =============================================================================
# Main Settings Class
# =============================================================================
class Settings(BaseSettings):
    # --- Values loaded from Azure App Configuration ---
    project: ProjectSettings
    agent: AgentSettings
    default_llm_model: str
    limits: dict[str, dict[str, int]]
    llm_tools: dict[str, LLMToolSetting]
    llm_prompts: dict[str, LLMPromptSetting]
    database: DatabaseSettings
    redis: RedisSettings
    cors: dict[str, list[str]]
    application: dict[str, str]
    frontend: FrontendSettings

    # --- Secrets resolved from Key Vault References by App Configuration ---
    SECRET_KEY: str
    DATABASE_PASSWORD: str
    OPENAI_API_KEY: str
    TURNSTILE_SECRET_KEY: str
    FAL_AI_KEY: str | None = None # CORRECTED: Added the FAL_AI_KEY

    # --- Computed Connection Strings ---
    @computed_field
    @property
    def DATABASE_URL(self) -> PostgresDsn:
        """Constructs the database connection string."""
        return PostgresDsn.build(
            scheme="postgresql+asyncpg",
            username=self.database.user,
            password=self.DATABASE_PASSWORD,
            host=self.database.host,
            port=self.database.port,
            path=self.database.db_name,
        )

    @computed_field
    @property
    def REDIS_URL(self) -> RedisDsn:
        """Constructs the Redis connection string."""
        return RedisDsn.build(
            scheme="redis",
            host=self.redis.host,
            port=self.redis.port,
            path=f"/{self.redis.db}",
        )


@lru_cache()
def get_settings() -> Settings:
    """
    Loads all settings from Azure App Configuration.
    Secrets are loaded via Key Vault References.
    """
    endpoint = os.getenv("APP_CONFIG_ENDPOINT")
    environment = os.getenv("APP_ENVIRONMENT")

    if not endpoint or not environment:
        raise ValueError(
            "APP_CONFIG_ENDPOINT and APP_ENVIRONMENT must be set."
        )

    # Use DefaultAzureCredential to authenticate
    credential = DefaultAzureCredential()
    client = AzureAppConfigurationClient(base_url=endpoint, credential=credential)

    # Fetch and resolve all settings, including those from Key Vault
    all_keys = client.list_configuration_settings(label_filter=environment)

    config_dict = {}
    for item in all_keys:
        keys = item.key.split(':')
        d = config_dict
        for key in keys[:-1]:
            d = d.setdefault(key, {})
        d[keys[-1]] = item.value

    # Pydantic validates that all keys, including secrets, were loaded
    return Settings(**config_dict)


# --- Global settings instance ---
settings = get_settings()