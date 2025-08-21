# backend/app/core/config.py
import os
from functools import lru_cache
from typing import List, Dict, Optional

from azure.appconfiguration import AzureAppConfigurationClient
from azure.identity import DefaultAzureCredential
from pydantic import BaseModel, PostgresDsn, RedisDsn, computed_field
from pydantic_settings import BaseSettings

# =============================================================================
# Pydantic Models for Configuration Structure
# =============================================================================
# These models define the expected structure of the configuration loaded from
# Azure App Configuration. This provides strong typing and validation.

class ProjectSettings(BaseModel):
    name: str
    api_prefix: str

class AgentSettings(BaseModel):
    max_retries: int

class LLMParams(BaseModel):
    temperature: float
    top_p: Optional[float] = None

class LLMToolSetting(BaseModel):
    model_name: strac
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
    fonts: Dict[str, str]

class FrontendContent(BaseModel):
    brand: Dict[str, str]
    footer: Dict[str, str | list]
    landingPage: Dict[str, str]
    finalPage: Dict[str, str]
    notFoundPage: Dict[str, str]
    notifications: Dict[str, str]
    loadingStates: Dict[str, str]
    errorStates: Dict[str, str]

class FrontendSettings(BaseModel):
    theme: FrontendTheme
    content: FrontendContent


# =============================================================================
# Main Settings Class
# =============================================================================
class Settings(BaseSettings):
    """
    The main settings class, which aggregates all configuration models.
    It loads values from Azure App Configuration and resolves secrets from
    Azure Key Vault via Key Vault References.
    """
    # --- Values loaded from Azure App Configuration ---
    project: ProjectSettings
    agent: AgentSettings
    default_llm_model: str
    limits: Dict[str, Dict[str, int]]
    llm_tools: Dict[str, LLMToolSetting]
    llm_prompts: Dict[str, LLMPromptSetting]
    database: DatabaseSettings
    redis: RedisSettings
    cors: Dict[str, List[str]]
    application: Dict[str, str]
    frontend: FrontendSettings

    # --- Secrets resolved from Key Vault References by App Configuration ---
    SECRET_KEY: str
    DATABASE_PASSWORD: str
    OPENAI_API_KEY: str
    TURNSTILE_SECRET_KEY: str
    FAL_AI_KEY: Optional[str] = None
    # FIX: Added missing GROQ_API_KEY to prevent crash in llm_service.py
    GROQ_API_KEY: Optional[str] = None

    # --- Computed Connection Strings ---
    @computed_field
    @property
    def DATABASE_URL(self) -> PostgresDsn:
        """
        Constructs the database connection string using string formatting,
        which is compatible with Pydantic v2.
        """
        # FIX: Replaced deprecated PostgresDsn.build() with string formatting.
        # Pydantic will still validate the final string against the PostgresDsn type.
        return (
            f"postgresql+asyncpg://{self.database.user}:{self.DATABASE_PASSWORD}"
            f"@{self.database.host}:{self.database.port}/{self.database.db_name}"
        )

    @computed_field
    @property
    def REDIS_URL(self) -> RedisDsn:
        """
        Constructs the Redis connection string using string formatting.
        """
        # FIX: Replaced deprecated RedisDsn.build() with string formatting.
        # Pydantic validates the final string against the RedisDsn type.
        return f"redis://{self.redis.host}:{self.redis.port}/{self.redis.db}"


@lru_cache()
def get_settings() -> Settings:
    """
    Loads all settings from Azure App Configuration for a given environment.
    This function is cached to ensure settings are loaded only once.
    It relies on environment variables for the App Configuration endpoint and
    the target environment label (e.g., 'dev', 'prod').
    """
    endpoint = os.getenv("APP_CONFIG_ENDPOINT")
    environment = os.getenv("APP_ENVIRONMENT")

    if not endpoint or not environment:
        raise ValueError(
            "Required environment variables APP_CONFIG_ENDPOINT and "
            "APP_ENVIRONMENT are not set. The application cannot start without them."
        )

    # Use DefaultAzureCredential, which supports various auth methods
    # (e.g., environment variables, managed identity) for connecting to Azure.
    credential = DefaultAzureCredential()
    client = AzureAppConfigurationClient(base_url=endpoint, credential=credential)

    # Fetch all configuration settings that match the specified environment label.
    # This includes plain key-values and Key Vault references.
    all_keys = client.list_configuration_settings(label_filter=environment)

    config_dict = {}
    for item in all_keys:
        # Convert colon-separated keys (e.g., "database:host") into a nested dict
        keys = item.key.split(':')
        d = config_dict
        for key in keys[:-1]:
            d = d.setdefault(key, {})
        d[keys[-1]] = item.value

    # Pydantic validates that all required keys, including secrets resolved
    # from Key Vault, were successfully loaded and parsed.
    return Settings(**config_dict)


# --- Global settings instance ---
# This instance is created once when the module is first imported and is reused
# throughout the application to ensure consistent configuration.
settings = get_settings()
