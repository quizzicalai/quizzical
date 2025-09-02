import os
from functools import lru_cache
from typing import List, Dict, Optional

from azure.appconfiguration import AzureAppConfigurationClient
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from pydantic import BaseModel, computed_field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# =============================================================================
# Pydantic Models for Configuration Structure
# =============================================================================

class ProjectSettings(BaseModel):
    """Defines project-level settings."""
    name: str = "Quizzical"
    api_prefix: str = "/api"

class AgentSettings(BaseModel):
    """Settings related to the agent's behavior."""
    max_retries: int = 5

class LLMParams(BaseModel):
    """Parameters for language model inference."""
    temperature: float = 0.7
    top_p: Optional[float] = None

class LLMToolSetting(BaseModel):
    """Configuration for a specific language model used as a tool."""
    model_name: str
    api_base: Optional[str] = None
    default_params: LLMParams = LLMParams()

class LLMPromptSetting(BaseModel):
    """Defines the structure for a system and user prompt."""
    system_prompt: str
    user_prompt_template: str

class DatabaseSettings(BaseModel):
    """Database connection settings."""
    host: str = "localhost"
    port: int = 5432
    user: str = "user"
    db_name: str = "quizzical"

class RedisSettings(BaseModel):
    """Redis connection settings."""
    host: str = "localhost"
    port: int = 6379
    db: int = 0

class FrontendThemeColors(BaseModel):
    """Defines the color palette for the frontend theme."""
    primary: str = "#6A1B9A"
    secondary: str = "#EC407A"
    accent: str = "#1DE9B6"
    muted: str = "#9E9E9E"
    background: str = "#F3E5F5"
    white: str = "#FFFFFF"

class FrontendTheme(BaseModel):
    """Defines the overall theme for the frontend."""
    colors: FrontendThemeColors = FrontendThemeColors()
    fonts: Dict[str, str] = {}

class FrontendContent(BaseModel):
    """Defines all user-facing content and copy for the frontend."""
    brand: Dict[str, str] = {}
    footer: Dict[str, str | list] = {}
    landingPage: Dict[str, str] = {}
    finalPage: Dict[str, str] = {}
    notFoundPage: Dict[str, str] = {}
    notifications: Dict[str, str] = {}
    loadingStates: Dict[str, str] = {}
    errorStates: Dict[str, str] = {}

class FrontendSettings(BaseModel):
    """Aggregates all frontend-related settings."""
    theme: FrontendTheme = FrontendTheme()
    content: FrontendContent = FrontendContent()


# =============================================================================
# Main Settings Class
# =============================================================================
class Settings(BaseSettings):
    """
    The main settings class, which aggregates all configuration models.
    It reads environment variables from a .env file and can be overridden
    by settings from Azure App Configuration and Azure Key Vault.
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter='__',
        extra="ignore"
    )

    APP_ENVIRONMENT: str = "local"
    APP_CONFIG_ENDPOINT: Optional[str] = None
    AZURE_KEY_VAULT_ENDPOINT: Optional[str] = None

    project: ProjectSettings = ProjectSettings()
    agent: AgentSettings = AgentSettings()
    default_llm_model: str = "gpt-4o"
    limits: Dict[str, Dict[str, int]] = {
        "quiz_requests": {"guest": 10, "user": 100},
        "image_generations": {"guest": 5, "user": 50},
    }
    
    # Provide minimal defaults for required fields
    llm_tools: Dict[str, LLMToolSetting] = {
        "default": LLMToolSetting(
            model_name="gpt-4o",
            default_params=LLMParams()
        ),
        "planner": LLMToolSetting(
            model_name="gpt-4o",
            default_params=LLMParams()
        ),
        "initial_planner": LLMToolSetting(
            model_name="gpt-4o",
            default_params=LLMParams()
        ),
        "synopsis_generator": LLMToolSetting(
            model_name="gpt-4o",
            default_params=LLMParams()
        ),
        "profile_writer": LLMToolSetting(
            model_name="gpt-4o",
            default_params=LLMParams()
        ),
        "profile_improver": LLMToolSetting(
            model_name="gpt-4o",
            default_params=LLMParams()
        ),
        "question_generator": LLMToolSetting(
            model_name="gpt-4o",
            default_params=LLMParams()
        ),
        "next_question_generator": LLMToolSetting(
            model_name="gpt-4o",
            default_params=LLMParams()
        ),
        "final_profile_writer": LLMToolSetting(
            model_name="gpt-4o",
            default_params=LLMParams()
        ),
        "character_list_generator": LLMToolSetting(
            model_name="gpt-4o",
            default_params=LLMParams()
        ),
        "character_selector": LLMToolSetting(
            model_name="gpt-4o",
            default_params=LLMParams()
        ),
        "image_prompt_enhancer": LLMToolSetting(
            model_name="gpt-4o",
            default_params=LLMParams()
        ),
        "safety_checker": LLMToolSetting(
            model_name="gpt-4o",
            default_params=LLMParams()
        ),
        "error_analyzer": LLMToolSetting(
            model_name="gpt-4o",
            default_params=LLMParams()
        ),
        "failure_explainer": LLMToolSetting(
            model_name="gpt-4o",
            default_params=LLMParams()
        ),
    }
    
    llm_prompts: Dict[str, LLMPromptSetting] = {}
    
    database: DatabaseSettings = DatabaseSettings()
    redis: RedisSettings = RedisSettings()
    
    # Fixed: Changed key from "allowed_origins" to "origins" to match main.py usage
    cors: Dict[str, List[str]] = {"origins": ["http://localhost:3000", "http://localhost:5137"]}
    
    application: Dict[str, str] = {"name": "Quizzical API"}
    
    frontend: FrontendSettings = FrontendSettings()

    ENABLE_TURNSTILE: bool = False

    # Secret values with defaults for local development
    SECRET_KEY: SecretStr = SecretStr("a_very_secret_key")
    DATABASE_PASSWORD: SecretStr = SecretStr("password")
    OPENAI_API_KEY: Optional[SecretStr] = None
    TURNSTILE_SECRET_KEY: Optional[SecretStr] = None
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
    Loads all settings, prioritizing Azure, with a fallback to .env files.
    This function is cached to ensure that settings are loaded only once.
    """
    # Start with settings from .env file - this reads ALL env vars
    settings = Settings()
    
    # Store the original .env-based settings for merging
    env_settings_dict = settings.model_dump()

    # Attempt to load from Azure App Configuration
    if settings.APP_CONFIG_ENDPOINT:
        try:
            credential = DefaultAzureCredential()
            client = AzureAppConfigurationClient(base_url=settings.APP_CONFIG_ENDPOINT, credential=credential)
            all_keys = client.list_configuration_settings(label_filter=settings.APP_ENVIRONMENT)

            config_dict = {}
            for item in all_keys:
                keys = item.key.split(':')
                d = config_dict
                for key in keys[:-1]:
                    d = d.setdefault(key, {})
                d[keys[-1]] = item.value

            # Merge: Start with .env settings, then overlay Azure settings
            # Azure values will override .env values where they exist
            merged_settings = {**env_settings_dict, **config_dict}
            settings = Settings(**merged_settings)
            print("Successfully loaded configuration from Azure App Configuration.")
        except Exception as e:
            print(f"WARNING: Could not connect to Azure App Configuration. Using .env settings. Error: {e}")
            # settings already contains .env values, so no action needed

    # Attempt to load secrets from Azure Key Vault
    if settings.AZURE_KEY_VAULT_ENDPOINT:
        try:
            credential = DefaultAzureCredential()
            client = SecretClient(vault_url=settings.AZURE_KEY_VAULT_ENDPOINT, credential=credential)
            
            # Helper to fetch secrets if they exist in the vault
            def get_secret(secret_name: str, default: Optional[SecretStr]) -> Optional[SecretStr]:
                try:
                    secret_value = client.get_secret(secret_name).value
                    return SecretStr(secret_value) if secret_value else default
                except Exception:
                    return default

            # Only override if Key Vault returns a value
            settings.DATABASE_PASSWORD = get_secret("db-password", settings.DATABASE_PASSWORD)
            settings.SECRET_KEY = get_secret("secret-key", settings.SECRET_KEY)
            settings.OPENAI_API_KEY = get_secret("openai-api-key", settings.OPENAI_API_KEY)
            settings.TURNSTILE_SECRET_KEY = get_secret("turnstile-secret-key", settings.TURNSTILE_SECRET_KEY)
            settings.FAL_AI_KEY = get_secret("fal-ai-key", settings.FAL_AI_KEY)
            settings.GROQ_API_KEY = get_secret("groq-api-key", settings.GROQ_API_KEY)

            print("Successfully loaded secrets from Azure Key Vault.")
        except Exception as e:
            print(f"WARNING: Could not connect to Azure Key Vault. Using .env secrets. Error: {e}")

    return settings