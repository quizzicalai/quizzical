import os
from functools import lru_cache
from typing import List, Dict, Optional

from azure.appconfiguration import AzureAppConfigurationClient
from azure.identity import DefaultAzureCredential
from pydantic import BaseModel, computed_field, SecretStr
from pydantic_settings import BaseSettings

# =============================================================================
# Pydantic Models for Configuration Structure
# =============================================================================

class ProjectSettings(BaseModel):
    """Defines project-level settings."""
    name: str
    api_prefix: str

class AgentSettings(BaseModel):
    """Settings related to the agent's behavior."""
    max_retries: int

class LLMParams(BaseModel):
    """Parameters for language model inference."""
    temperature: float
    top_p: Optional[float] = None

class LLMToolSetting(BaseModel):
    """Configuration for a specific language model used as a tool."""
    model_name: str
    api_base: Optional[str] = None
    default_params: LLMParams

class LLMPromptSetting(BaseModel):
    """Defines the structure for a system and user prompt."""
    system_prompt: str
    user_prompt_template: str
    
class DatabaseSettings(BaseModel):
    """Database connection settings."""
    host: str
    port: int
    user: str
    db_name: str

class RedisSettings(BaseModel):
    """Redis connection settings."""
    host: str
    port: int
    db: int

class FrontendThemeColors(BaseModel):
    """Defines the color palette for the frontend theme."""
    primary: str
    secondary: str
    accent: str
    muted: str
    background: str
    white: str

class FrontendTheme(BaseModel):
    """Defines the overall theme for the frontend."""
    colors: FrontendThemeColors
    fonts: Dict[str, str]

class FrontendContent(BaseModel):
    """Defines all user-facing content and copy for the frontend."""
    brand: Dict[str, str]
    footer: Dict[str, str | list]
    landingPage: Dict[str, str]
    finalPage: Dict[str, str]
    notFoundPage: Dict[str, str]
    notifications: Dict[str, str]
    loadingStates: Dict[str, str]
    errorStates: Dict[str, str]

class FrontendSettings(BaseModel):
    """Aggregates all frontend-related settings."""
    theme: FrontendTheme
    content: FrontendContent


# =============================================================================
# Main Settings Class
# =============================================================================
class Settings(BaseSettings):
    """
    The main settings class, which aggregates all configuration models.
    It reads environment variables and loads the main configuration from
    Azure App Configuration.
    """
    # FIX: Added APP_ENVIRONMENT and APP_CONFIG_ENDPOINT to be managed by Pydantic
    # This provides a single source of truth for all settings.
    APP_ENVIRONMENT: str = "local"
    APP_CONFIG_ENDPOINT: Optional[str] = None

    # Nested configuration models loaded from Azure
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

    # FIX: Added ENABLE_TURNSTILE with a safe default for local development.
    # This prevents the application from crashing if the setting is not
    # explicitly defined in the configuration source.
    ENABLE_TURNSTILE: bool = False

    # Secret values, loaded from the configuration source
    SECRET_KEY: SecretStr
    DATABASE_PASSWORD: SecretStr
    OPENAI_API_KEY: Optional[SecretStr] = None
    TURNSTILE_SECRET_KEY: SecretStr
    FAL_AI_KEY: Optional[SecretStr] = None
    GROQ_API_KEY: Optional[SecretStr] = None

    @computed_field
    @property
    def DATABASE_URL(self) -> str:
        """Computes the full database connection string."""
        return (
            f"postgresql+asyncpg://{self.database.user}:{self.DATABASE_PASSWORD.get_secret_value()}"
            f"@{self.database.host}:{self.database.port}/{self.database.db_name}"
        )

    @computed_field
    @property
    def REDIS_URL(self) -> str:
        """Computes the full Redis connection string."""
        return f"redis://{self.redis.host}:{self.redis.port}/{self.redis.db}"


@lru_cache()
def get_settings() -> Settings:
    """
    Loads all settings from environment variables and Azure App Configuration.
    
    This function is cached to ensure that settings are loaded only once.
    """
    # First, load settings from environment variables
    env_settings = Settings.model_validate({})

    # FIX: Improved environment variable handling with a clear, developer-friendly error.
    # The application will now fail fast with an explicit message if the critical
    # endpoint configuration is missing.
    endpoint = env_settings.APP_CONFIG_ENDPOINT
    environment = env_settings.APP_ENVIRONMENT

    if not endpoint:
        raise ValueError(
            "FATAL: The 'APP_CONFIG_ENDPOINT' environment variable is not set. "
            "This is required to connect to Azure App Configuration. Please set it in your "
            ".env file or environment."
        )

    # Proceed to load the main configuration from Azure
    try:
        credential = DefaultAzureCredential()
        client = AzureAppConfigurationClient(base_url=endpoint, credential=credential)

        all_keys = client.list_configuration_settings(label_filter=environment)

        config_dict = {}
        for item in all_keys:
            # Reconstruct nested dictionary from flattened keys (e.g., "project:name")
            keys = item.key.split(':')
            d = config_dict
            for key in keys[:-1]:
                d = d.setdefault(key, {})
            d[keys[-1]] = item.value
        
        # Merge environment settings with the settings loaded from Azure
        # and validate the final configuration.
        final_settings_data = {**env_settings.model_dump(), **config_dict}
        return Settings(**final_settings_data)

    except Exception as e:
        print(f"FATAL: Could not connect to or parse settings from Azure App Configuration at '{endpoint}'.")
        raise e


# Create a single, globally accessible settings instance
settings = get_settings()
