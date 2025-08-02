"""
LLM Service

This service provides a unified, resilient, and configuration-driven interface
for all Large Language Model (LLM) interactions.

It uses the `litellm` library to abstract away the differences between various
LLM providers (e.g., OpenAI, Groq, Anthropic), allowing the application to
use the best model for each specific task, configured dynamically.
"""
import json
from typing import Any, Dict, List, Optional, Type, TypeVar

import litellm
import structlog
from langchain_core.messages import AIMessage
from pydantic import BaseModel, ValidationError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import LLMToolConfig, settings

# Get a logger that is already configured with structlog
logger = structlog.get_logger(__name__)

# Define a TypeVar for Pydantic models to improve type hinting
PydanticModel = TypeVar("PydanticModel", bound=BaseModel)

# --- Custom Application-Specific Exception ---

class StructuredOutputError(Exception):
    """Custom exception raised when the LLM output fails Pydantic validation."""
    pass

# --- litellm Global Configuration ---

RETRYABLE_EXCEPTIONS = (
    litellm.exceptions.APITimeoutError,
    litellm.exceptions.APIConnectionError,
    litellm.exceptions.RateLimitError,
    litellm.exceptions.ServiceUnavailableError,
)

class StructlogCallback(litellm.Callback):
    """Integrates litellm logging with structlog for observability and cost tracking."""
    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        logger.info(
            "llm_call_success",
            model=kwargs.get("model"),
            total_time_ms=int((end_time - start_time).total_seconds() * 1000),
            usage=response_obj.usage.model_dump() if response_obj.usage else None,
            cost_usd=response_obj.cost,
        )

    def log_failure_event(self, kwargs, original_exception, start_time, end_time):
        logger.error(
            "llm_call_failure",
            model=kwargs.get("model"),
            total_time_ms=int((end_time - start_time).total_seconds() * 1000),
            error_type=type(original_exception).__name__,
            error_message=str(original_exception),
        )

# --- LLM Service (Singleton Pattern) ---

class LLMService:
    """A service class for making resilient, configuration-driven calls to LLMs."""

    def __init__(self):
        litellm.set_verbose = False
        litellm.callbacks = [StructlogCallback()]
        litellm.api_timeout = 300
        self.api_key_map = {
            "groq": settings.GROQ_API_KEY,
            "openai": settings.OPENAI_API_KEY,
        }

    def _get_tool_config(self, tool_name: str) -> LLMToolConfig:
        return settings.llm_tools.get(tool_name, settings.llm_tools["default"])

    def _get_api_key(self, model_name: str) -> str:
        provider = model_name.split("/")[0] if "/" in model_name else "openai"
        secret = self.api_key_map.get(provider, self.api_key_map["openai"])
        return secret.get_secret_value()

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(4),
        retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
    )
    async def get_agent_response(
        self,
        tool_name: str,
        messages: List[Dict[str, str]],
        tools: List[Dict[str, Any]],
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
        **kwargs: Any,
    ) -> AIMessage:
        """
        Makes a resilient call to an LLM for the agent's planner, expecting
        a response that may include tool calls.
        """
        config = self._get_tool_config(tool_name)
        api_key = self._get_api_key(config.model_name)
        api_params = {**config.default_params, **kwargs}
        metadata = {"trace_id": trace_id, "session_id": session_id}

        response = await litellm.acompletion(
            model=config.model_name,
            messages=messages,
            tools=tools,
            api_key=api_key,
            api_base=config.api_base,
            metadata=metadata,
            **api_params,
        )

        # Convert the litellm response to a LangChain AIMessage for the graph
        response_message = response.choices[0].message
        tool_calls = []
        if response_message.tool_calls:
            tool_calls = [
                {
                    "id": call.id,
                    "name": call.function.name,
                    "args": json.loads(call.function.arguments),
                }
                for call in response_message.tool_calls
            ]
        
        return AIMessage(
            content=response_message.content or "",
            tool_calls=tool_calls,
        )

    # ... (get_text_response and get_structured_response remain the same) ...

# Create a single instance of the service for the application
llm_service = LLMService()
