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

from app.core.config import LLMToolSetting, settings

# Get a logger that is already configured with structlog
logger = structlog.get_logger(__name__)

# Define a TypeVar for Pydantic models to improve type hinting
PydanticModel = TypeVar("PydanticModel", bound=BaseModel)

# --- Custom Application-Specific Exceptions ---

class LLMAPIError(Exception):
    """Base exception for LLM API related errors."""
    pass

class StructuredOutputError(LLMAPIError):
    """Custom exception raised when the LLM output fails Pydantic validation."""
    pass

class ContentFilteringError(LLMAPIError):
    """Custom exception raised when a request is blocked by content filters."""
    pass


# --- litellm Global Configuration ---

# Define exceptions that are safe to retry on
RETRYABLE_EXCEPTIONS = (
    litellm.exceptions.APITimeoutError,
    litellm.exceptions.APIConnectionError,
    litellm.exceptions.RateLimitError,
    litellm.exceptions.ServiceUnavailableError,
    litellm.exceptions.APIError, # Retry on generic 500s from the API
)

class StructlogCallback(litellm.Callback):
    """Integrates litellm logging with structlog for observability and cost tracking."""
    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        # Extract custom metadata passed to the litellm call
        metadata = kwargs.get("metadata", {})
        logger.info(
            "llm_call_success",
            model=kwargs.get("model"),
            tool_name=metadata.get("tool_name"),
            trace_id=metadata.get("trace_id"),
            total_time_ms=int((end_time - start_time).total_seconds() * 1000),
            usage=response_obj.usage.model_dump() if response_obj.usage else None,
            cost_usd=response_obj.cost,
        )

    def log_failure_event(self, kwargs, original_exception, start_time, end_time):
        metadata = kwargs.get("metadata", {})
        logger.error(
            "llm_call_failure",
            model=kwargs.get("model"),
            tool_name=metadata.get("tool_name"),
            trace_id=metadata.get("trace_id"),
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
        litellm.api_timeout = 300 # 5-minute timeout for potentially long generations
        self.api_key_map = {
            "groq": settings.GROQ_API_KEY.get_secret_value() if settings.GROQ_API_KEY else None,
            "openai": settings.OPENAI_API_KEY.get_secret_value() if settings.OPENAI_API_KEY else None,
        }

    def _get_tool_config(self, tool_name: str) -> LLMToolSetting:
        """Retrieves the configuration for a specific tool, falling back to default."""
        return settings.llm_tools.get(tool_name, settings.llm_tools["default"])

    def _get_api_key(self, model_name: str) -> Optional[str]:
        """Determines the correct API key based on the model name."""
        provider = model_name.split("/")[0] if "/" in model_name else "openai"
        return self.api_key_map.get(provider)

    def _build_messages(self, tool_name: str, prompt_kwargs: Dict[str, Any]) -> List[Dict[str, str]]:
        """Constructs the message list from configured templates."""
        prompt_config = settings.llm_prompts.get(tool_name)
        if not prompt_config:
            raise ValueError(f"No prompt configuration found for tool: {tool_name}")

        system_prompt = prompt_config.system_prompt.format(**prompt_kwargs)
        user_prompt = prompt_config.user_prompt_template.format(**prompt_kwargs)

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(settings.agent.max_retries),
        retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
        reraise=True, # Re-raise the exception after the final attempt
    )
    async def invoke_llm(
        self,
        tool_name: str,
        prompt_kwargs: Dict[str, Any],
        response_model: Optional[Type[PydanticModel]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        trace_id: Optional[str] = None,
    ) -> Any:
        """
        A unified, resilient method for all LLM invocations.

        Args:
            tool_name: The name of the tool configuration to use.
            prompt_kwargs: A dictionary of values to format the prompt templates.
            response_model: An optional Pydantic model for structured JSON output.
            tools: An optional list of tools for the agent planner.
            trace_id: An optional trace ID for observability.

        Returns:
            The LLM's response, which could be a string, a Pydantic model instance,
            or a LangChain AIMessage.
        """
        config = self._get_tool_config(tool_name)
        api_key = self._get_api_key(config.model_name)
        messages = self._build_messages(tool_name, prompt_kwargs)
        
        litellm_kwargs = {
            "model": config.model_name,
            "messages": messages,
            "api_key": api_key,
            "api_base": config.api_base,
            "metadata": {"tool_name": tool_name, "trace_id": trace_id},
            **config.default_params,
        }

        if response_model:
            litellm_kwargs["response_model"] = {"model": response_model}
        if tools:
            litellm_kwargs["tools"] = tools

        try:
            response = await litellm.acompletion(**litellm_kwargs)
        except ValidationError as e:
            logger.error("LLM output failed Pydantic validation", tool_name=tool_name, error=str(e))
            raise StructuredOutputError(f"LLM output validation failed for {tool_name}: {e}")
        except litellm.exceptions.ContentPolicyViolationError as e:
            logger.warning("LLM call blocked by content policy", tool_name=tool_name, error=str(e))
            raise ContentFilteringError(f"Request for {tool_name} was blocked by content filters.")
        except Exception as e:
            logger.error("An unexpected error occurred during LLM call", tool_name=tool_name, error=str(e))
            raise LLMAPIError(f"An unexpected API error occurred for {tool_name}: {e}")

        # --- Process and return the response based on the request type ---
        response_message = response.choices[0].message

        if tools: # Agent planner call
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
            return AIMessage(content=response_message.content or "", tool_calls=tool_calls)
        
        if response_model: # Structured JSON call
            return response.choices[0].message.content

        # Default to plain text response
        return response.choices[0].message.content

# Create a single instance of the service for the application
llm_service = LLMService()
