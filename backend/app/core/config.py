import os
from functools import lru_cache
from typing import List, Dict, Optional

from azure.appconfiguration import AzureAppConfigurationClient
from azure.identity import DefaultAzureCredential
from pydantic import BaseModel, PostgresDsn, RedisDsn, computed_field, SecretStr
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
    top_p: Optional[float] = None

class LLMToolSetting(BaseModel):
    model_name: str
    # FIX: Added optional api_base to support models requiring a custom endpoint.
    api_base: Optional[str] = None
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
    """
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

    SECRET_KEY: SecretStr
    DATABASE_PASSWORD: SecretStr
    OPENAI_API_KEY: Optional[SecretStr] = None
    TURNSTILE_SECRET_KEY: SecretStr
    FAL_AI_KEY: Optional[SecretStr] = None
    GROQ_API_KEY: Optional[SecretStr] = None

    @computed_field
    @property
    def DATABASE_URL(self) -> str:
        return (
            f"postgresql+asyncpg://{self.database.user}:{self.DATABASE_PASSWORD.get_secret_value()}"
            f"@{self.database.host}:{self.database.port}/{self.database.db_name}"
        )

    @computed_field
    @property
    def REDIS_URL(self) -> str:
        return f"redis://{self.redis.host}:{self.redis.port}/{self.redis.db}"


@lru_cache()
def get_settings() -> Settings:
    """
    Loads all settings from Azure App Configuration for a given environment.
    """
    endpoint = os.getenv("APP_CONFIG_ENDPOINT")
    environment = os.getenv("APP_ENVIRONMENT")

    if not endpoint or not environment:
        raise ValueError(
            "Required environment variables APP_CONFIG_ENDPOINT and "
            "APP_ENVIRONMENT are not set."
        )

    credential = DefaultAzureCredential()
    client = AzureAppConfigurationClient(base_url=endpoint, credential=credential)

    all_keys = client.list_configuration_settings(label_filter=environment)

    config_dict = {}
    for item in all_keys:
        keys = item.key.split(':')
        d = config_dict
        for key in keys[:-1]:
            d = d.setdefault(key, {})
        d[keys[-1]] = item.value

    return Settings(**config_dict)

settings = get_settings()
