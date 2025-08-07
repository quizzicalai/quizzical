"""
LLM Service

This service provides a unified, resilient, and configuration-driven interface
for all Large Language Model (LLM) interactions.

It uses the `litellm` library to abstract away the differences between various
LLM providers and the `langchain_core` library for message and tool structures.
This allows the application to use the best model for each specific task,
configured dynamically, while maintaining a consistent interface.
"""
import json
from typing import Any, Dict, List, Optional, Type, TypeVar

import litellm
import structlog
from langchain_core.messages import AIMessage, BaseMessage
from pydantic import BaseModel, ValidationError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import LLMToolSetting, settings

# --- Setup ---
logger = structlog.get_logger(__name__)
PydanticModel = TypeVar("PydanticModel", bound=BaseModel)

# --- Custom Application-Specific Exceptions ---
class LLMAPIError(Exception):
    """Base exception for all LLM API related errors."""
    pass

class StructuredOutputError(LLMAPIError):
    """Raised when the LLM output fails Pydantic validation."""
    pass

class ContentFilteringError(LLMAPIError):
    """Raised when a request is blocked by content filters."""
    pass

# --- litellm Global Configuration ---
RETRYABLE_EXCEPTIONS = (
    litellm.exceptions.APITimeoutError,
    litellm.exceptions.APIConnectionError,
    litellm.exceptions.RateLimitError,
    litellm.exceptions.ServiceUnavailableError,
)

class StructlogCallback(litellm.Callback):
    """Integrates litellm logging with structlog for unified observability."""
    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        metadata = kwargs.get("metadata", {})
        logger.info(
            "llm_call_success",
            model=kwargs.get("model"),
            tool_name=metadata.get("tool_name"),
            trace_id=metadata.get("trace_id"),
            duration_ms=int((end_time - start_time).total_seconds() * 1000),
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
            duration_ms=int((end_time - start_time).total_seconds() * 1000),
            error_type=type(original_exception).__name__,
            error_message=str(original_exception),
        )

# --- LLM Service (Singleton Pattern) ---
class LLMService:
    """A service class for making resilient, configuration-driven calls to LLMs."""

    def __init__(self):
        litellm.set_verbose = False
        litellm.callbacks = [StructlogCallback()]
        self.api_key_map = {
            "groq": settings.GROQ_API_KEY.get_secret_value() if settings.GROQ_API_KEY else None,
            "openai": settings.OPENAI_API_KEY.get_secret_value() if settings.OPENAI_API_KEY else None,
        }

    def _get_config(self, tool_name: str) -> LLMToolSetting:
        """Retrieves the configuration for a specific tool."""
        return settings.llm_tools.get(tool_name, settings.llm_tools["default"])

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(settings.agent.max_retries),
        retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
        reraise=True,
    )
    async def _invoke(self, litellm_kwargs: Dict[str, Any]) -> litellm.ModelResponse:
        """Private method to execute the litellm call with retry logic."""
        try:
            return await litellm.acompletion(**litellm_kwargs)
        except ValidationError as e:
            logger.error("LLM output failed Pydantic validation", **litellm_kwargs.get("metadata", {}), error=str(e))
            raise StructuredOutputError(f"LLM output validation failed: {e}")
        except litellm.exceptions.ContentPolicyViolationError as e:
            logger.warning("LLM call blocked by content policy", **litellm_kwargs.get("metadata", {}), error=str(e))
            raise ContentFilteringError(f"Request was blocked by content filters.")
        except Exception as e:
            logger.error("An unexpected error occurred during LLM call", **litellm_kwargs.get("metadata", {}), error=str(e))
            raise LLMAPIError(f"An unexpected API error occurred: {e}")

    def _prepare_request(
        self,
        tool_name: str,
        messages: List[BaseMessage],
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Prepares the kwargs for the litellm call."""
        config = self._get_config(tool_name)
        api_key = self.api_key_map.get(config.model_name.split('/')[0], None)

        return {
            "model": config.model_name,
            "messages": [m.dict() for m in messages],
            "api_key": api_key,
            "api_base": config.api_base,
            "metadata": {"tool_name": tool_name, "trace_id": trace_id, "session_id": session_id},
            **config.default_params,
        }

    async def get_agent_response(
        self,
        tool_name: str,
        messages: List[BaseMessage],
        tools: List[Dict[str, Any]],
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> AIMessage:
        """Gets a response for the main agent planner, expecting tool calls."""
        request_kwargs = self._prepare_request(tool_name, messages, trace_id, session_id)
        request_kwargs["tools"] = tools

        response = await self._invoke(request_kwargs)
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
        return AIMessage(content=response_message.content or "", tool_calls=tool_calls)

    async def get_structured_response(
        self,
        tool_name: str,
        messages: List[BaseMessage],
        response_model: Type[PydanticModel],
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> PydanticModel:
        """Gets a response structured into a Pydantic model."""
        request_kwargs = self._prepare_request(tool_name, messages, trace_id, session_id)
        request_kwargs["response_model"] = {"model": response_model}

        response = await self._invoke(request_kwargs)
        return response.choices[0].message.content


# --- Factory Function ---
def get_llm_service() -> LLMService:
    """Returns a singleton instance of the LLMService."""
    return LLMService()

# Create a single instance for the application to use
llm_service = get_llm_service()