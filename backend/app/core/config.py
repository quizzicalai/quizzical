"""
Application Configuration

This module defines the Pydantic Settings model for managing all application
configuration. It loads settings from environment variables, which can be
populated by a .env file locally or by Azure App Configuration in production.
"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMToolConfig(BaseModel):
    """Defines the configuration for a specific LLM tool/task."""
    model_name: str
    api_key: Optional[SecretStr] = None
    api_base: Optional[str] = None
    default_params: Dict[str, Any] = {}


class LLMPromptConfig(BaseModel):
    """Defines the system and user prompt templates for a tool."""
    system_prompt: str
    user_prompt_template: str


class Settings(BaseSettings):
    """
    Main settings model for the application.
    Reads from environment variables (case-insensitive).
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
    )

    # Core Service Credentials
    DATABASE_URL: SecretStr
    REDIS_URL: SecretStr
    OPENAI_API_KEY: SecretStr

    # Security & CORS
    # In production, this should be set to the frontend's domain,
    # e.g., "https://www.yourapp.com"
    ALLOWED_ORIGINS: Optional[List[str]] = None

    # Default LLM model to use if not specified by a tool.
    DEFAULT_LLM_MODEL: str = "gpt-4o"

    # LLM Tool and Prompt Configurations
    llm_tools: Dict[str, LLMToolConfig] = {}
    llm_prompts: Dict[str, LLMPromptConfig] = {}

    @model_validator(mode="after")
    def set_default_configs(self) -> "Settings":
        if not self.llm_tools:
            self.llm_tools = {
                "default": LLMToolConfig(model_name=self.DEFAULT_LLM_MODEL),
                "planner": LLMToolConfig(model_name=self.DEFAULT_LLM_MODEL),
                "profile_writer": LLMToolConfig(model_name=self.DEFAULT_LLM_MODEL),
                "judge": LLMToolConfig(model_name=self.DEFAULT_LLM_MODEL),
            }
        
        if not self.llm_prompts:
            self.llm_prompts = {
                "synopsis_writer": LLMPromptConfig(
                    system_prompt="You are an expert at semantic analysis...",
                    user_prompt_template="Please generate a synopsis for the category: '{category}'..."
                ),
                "planner": LLMPromptConfig(
                    system_prompt="You are a master quiz planner...",
                    user_prompt_template="Category: {category}\nSynopsis: {synopsis}\nHistorical Context:\n{rag_context}"
                ),
            }
        return self

settings = Settings()