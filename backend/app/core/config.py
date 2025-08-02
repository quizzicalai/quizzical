"""
Application Configuration

This module defines the Pydantic Settings model for managing all application
configuration. It loads settings from environment variables, which can be
populated by a .env file locally or by Azure App Configuration in production.
"""
from typing import Any, Dict, Optional

from pydantic import BaseModel, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMToolConfig(BaseModel):
    """Defines the configuration for a specific LLM tool/task."""

    model_name: str  # The model name is now required, no hardcoded default.
    api_key: Optional[SecretStr] = None
    api_base: Optional[str] = None
    default_params: Dict[str, Any] = {}


class Settings(BaseSettings):
    """
    Main settings model for the application.
    Reads from environment variables (case-insensitive).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",  # Allows for nested config like LLM_TOOLS__PLANNER__MODEL_NAME
    )

    # Core Service Credentials
    DATABASE_URL: SecretStr
    REDIS_URL: SecretStr
    OPENAI_API_KEY: SecretStr

    # Default LLM model to use if not specified by a tool.
    # This is the single source of truth for the default model.
    DEFAULT_LLM_MODEL: str = "gpt-4o"

    # LLM Tool Configurations
    # This is initialized as an empty dict and populated by the validator below.
    llm_tools: Dict[str, LLMToolConfig] = {}

    @model_validator(mode="after")
    def set_default_tools(self) -> "Settings":
        """
        This validator runs after the model is created and allows us to
        set complex default values that depend on other fields. It populates
        the llm_tools dictionary if it wasn't provided via environment variables.
        """
        if not self.llm_tools:
            # By default, all tools will use the DEFAULT_LLM_MODEL.
            # This can be overridden for any specific tool via environment variables,
            # e.g., LLM_TOOLS__JUDGE__MODEL_NAME="some-other-model"
            self.llm_tools = {
                "default": LLMToolConfig(model_name=self.DEFAULT_LLM_MODEL),
                "planner": LLMToolConfig(model_name=self.DEFAULT_LLM_MODEL),
                "profile_writer": LLMToolConfig(model_name=self.DEFAULT_LLM_MODEL),
                "judge": LLMToolConfig(model_name=self.DEFAULT_LLM_MODEL),
            }
        return self