"""
LLM Service

This service provides a unified, resilient, and configuration-driven interface
for all Large Language Model (LLM) interactions.

Changes for RAG:
- Embeddings now use a local HuggingFace sentence-transformers model (configurable)
  instead of remote APIs. This keeps RAG non-blocking and privacy-friendly.
- Embedding calls are tolerant: on any failure, return [] (callers already handle
  empty / missing embeddings gracefully).
"""
from __future__ import annotations

import json
import os
import threading
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

logger = structlog.get_logger(__name__)
PydanticModel = TypeVar("PydanticModel", bound=BaseModel)


class LLMAPIError(Exception):
    """Base exception for all LLM API related errors."""
    pass


class StructuredOutputError(LLMAPIError):
    """Raised when the LLM output fails Pydantic validation."""
    pass


class ContentFilteringError(LLMAPIError):
    """Raised when a request is blocked by content filters."""
    pass


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
            usage=response_obj.usage.model_dump() if getattr(response_obj, "usage", None) else None,
            cost_usd=getattr(response_obj, "cost", None),
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


# -----------------------------
# HuggingFace embedding support
# -----------------------------
# Lazy-initialized singleton SentenceTransformer model (thread-safe).
_embed_model = None
_embed_lock = threading.Lock()
_embed_import_error: Optional[str] = None  # remember import failure to avoid repeated attempts


def _get_embedding_config() -> Dict[str, Any]:
    """
    Pull embedding configuration from settings or environment.
    Falls back to sensible defaults for local development.
    """
    # Prefer nested settings if present; otherwise read from env
    model_name = os.getenv("EMBEDDING__MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2")
    dim_str = os.getenv("EMBEDDING__DIM", "384")
    distance = os.getenv("EMBEDDING__DISTANCE_METRIC", "cosine")
    column = os.getenv("EMBEDDING__COLUMN", "synopsis_embedding")
    try:
        dim = int(dim_str)
    except Exception:
        dim = 384
    return {
        "model_name": model_name,
        "dim": dim,
        "distance": distance,
        "column": column,
    }


def _ensure_hf_model():
    """
    Ensure the global HuggingFace SentenceTransformer model is loaded.
    If sentence-transformers is not installed or init fails, record the error.
    """
    global _embed_model, _embed_import_error
    if _embed_model is not None or _embed_import_error is not None:
        return

    with _embed_lock:
        if _embed_model is not None or _embed_import_error is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except Exception as e:  # pragma: no cover
            _embed_import_error = f"sentence-transformers import failed: {e}"
            logger.warn("Embedding disabled: sentence-transformers not available", error=str(e))
            return

        cfg = _get_embedding_config()
        model_name = cfg["model_name"]
        try:
            # Default to CPU; MPS/GPU can be enabled later without code change.
            _embed_model = SentenceTransformer(model_name, device="cpu")
            logger.info("HuggingFace embedding model loaded", model_name=model_name)
        except Exception as e:  # pragma: no cover
            _embed_import_error = f"SentenceTransformer load failed: {e}"
            logger.error("Failed to load HuggingFace embedding model", model_name=model_name, error=str(e))


class LLMService:
    """A service class for making resilient, configuration-driven calls to LLMs."""

    def __init__(self):
        litellm.set_verbose = False
        litellm.callbacks = [StructlogCallback()]
        self.api_key_map = {
            "groq": settings.GROQ_API_KEY.get_secret_value() if getattr(settings, "GROQ_API_KEY", None) else None,
            "openai": settings.OPENAI_API_KEY.get_secret_value() if getattr(settings, "OPENAI_API_KEY", None) else None,
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
            logger.error(
                "LLM output failed Pydantic validation",
                **litellm_kwargs.get("metadata", {}),
                error=str(e),
            )
            raise StructuredOutputError(f"LLM output validation failed: {e}")
        except litellm.exceptions.ContentPolicyViolationError as e:
            logger.warning(
                "LLM call blocked by content policy",
                **litellm_kwargs.get("metadata", {}),
                error=str(e),
            )
            raise ContentFilteringError("Request was blocked by content filters.")
        except Exception as e:
            logger.error(
                "An unexpected error occurred during LLM call",
                **litellm_kwargs.get("metadata", {}),
                error=str(e),
            )
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
        model_provider = config.model_name.split("/")[0]
        api_key = self.api_key_map.get(model_provider, None)

        return {
            "model": config.model_name,
            "messages": [m.model_dump() for m in messages],
            "api_key": api_key,
            "api_base": config.api_base,
            "metadata": {
                "tool_name": tool_name,
                "trace_id": trace_id,
                "session_id": session_id,
            },
            **config.default_params.model_dump(),
        }

    async def get_agent_response(
        self,
        tool_name: str,
        messages: List[BaseMessage],
        tools: List[Dict[str, Any]],
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> AIMessage:
        """
        Gets a response for the main agent planner, expecting tool calls.
        Dynamically enables OpenAI's built-in web search tool (kept as-is).
        """
        request_kwargs = self._prepare_request(tool_name, messages, trace_id, session_id)

        config = self._get_config(tool_name)
        if config.model_name.startswith("gpt-") or config.model_name.startswith("o4-"):
            request_kwargs["tools"] = tools + [{"type": "web_search"}]
            logger.info("Enabled OpenAI web search tool for this request.")
        else:
            request_kwargs["tools"] = tools

        response = await self._invoke(request_kwargs)
        response_message = response.choices[0].message

        tool_calls = []
        if getattr(response_message, "tool_calls", None):
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
        request_kwargs["response_model"] = response_model

        response = await self._invoke(request_kwargs)
        # When using response_model, litellm returns the pydantic object directly
        return response.choices[0].message.content

    async def get_text_response(
        self,
        tool_name: str,
        messages: List[BaseMessage],
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> str:
        """Gets a simple text response from the LLM."""
        request_kwargs = self._prepare_request(tool_name, messages, trace_id, session_id)
        response = await self._invoke(request_kwargs)
        content = response.choices[0].message.content
        return content if isinstance(content, str) else ""

    async def get_embedding(
        self, input: List[str], model: str | None = None
    ) -> List[List[float]]:
        """
        Generates embeddings for a list of texts using a local HuggingFace model.
        - Non-blocking: returns [] on any error.
        - Ignores the `model` param (kept for backward compatibility); uses config/env.
        """
        if not input:
            return []

        cfg = _get_embedding_config()
        # Ensure model is loaded (lazy)
        _ensure_hf_model()

        if _embed_model is None:
            # If sentence-transformers is unavailable or failed to load, be tolerant
            logger.warn(
                "Embedding unavailable; returning empty embeddings",
                reason=_embed_import_error or "unknown",
            )
            return []

        # Offload to thread executor to avoid blocking the event loop
        try:
            import asyncio

            loop = asyncio.get_running_loop()

            def _encode(texts: List[str]) -> List[List[float]]:
                vecs = _embed_model.encode(texts, normalize_embeddings=True)  # cosine-friendly
                # SentenceTransformer returns np.ndarray; convert to list of lists
                if hasattr(vecs, "tolist"):
                    return vecs.tolist()
                # Edge case: single vector
                return [list(vecs)] if isinstance(vecs, (list, tuple)) else []

            embeddings: List[List[float]] = await loop.run_in_executor(None, _encode, input)

            # Optional: warn if dimension mismatch vs DB config (does not fail)
            if embeddings and len(embeddings[0]) != int(cfg["dim"]):
                logger.warn(
                    "Embedding dimension differs from configured dim; consider aligning model and DB column",
                    got=len(embeddings[0]),
                    expected=int(cfg["dim"]),
                    column=cfg["column"],
                    model_name=cfg["model_name"],
                )
            return embeddings
        except Exception as e:
            logger.error("Embedding generation failed", error=str(e), exc_info=True)
            return []


def get_llm_service() -> LLMService:
    """Returns a singleton instance of the LLMService."""
    return LLMService()


llm_service = get_llm_service()
