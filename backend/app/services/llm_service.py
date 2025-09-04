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

import asyncio
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

# ---- Logging / Types --------------------------------------------------------

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
    litellm.exceptions.Timeout,
    litellm.exceptions.APIConnectionError,
    litellm.exceptions.RateLimitError,
    litellm.exceptions.ServiceUnavailableError,
)

# ---- LiteLLM callbacks (switch to CustomLogger + completion_cost) -----------

try:
    from litellm.integrations.custom_logger import CustomLogger
except Exception:  # pragma: no cover
    # Fallback so imports don't break tests if integrations aren't present.
    class CustomLogger:  # type: ignore
        pass


class StructlogCallback(CustomLogger):
    """Integrates LiteLLM logging with structlog for unified observability."""

    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        metadata = kwargs.get("metadata", {}) or {}
        # Use the supported cost helper; do NOT rely on response_obj.cost
        try:
            cost = litellm.completion_cost(completion_response=response_obj)
        except Exception:
            cost = None

        usage = getattr(response_obj, "usage", None)
        usage_dict = None
        if usage is not None:
            # usage is a pydantic-like object in LiteLLM responses
            usage_dict = (
                usage.model_dump()
                if hasattr(usage, "model_dump")
                else getattr(usage, "__dict__", None)
            )

        logger.info(
            "llm_call_success",
            model=kwargs.get("model"),
            tool_name=metadata.get("tool_name"),
            trace_id=metadata.get("trace_id"),
            duration_ms=int((end_time - start_time).total_seconds() * 1000),
            usage=usage_dict,
            cost_usd=float(cost) if cost is not None else None,
        )

    def log_failure_event(self, kwargs, original_exception, start_time, end_time):
        metadata = kwargs.get("metadata", {}) or {}
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
            _embed_model = SentenceTransformer(model_name, device="cpu")
            logger.info("HuggingFace embedding model loaded", model_name=model_name)
        except Exception as e:  # pragma: no cover
            _embed_import_error = f"SentenceTransformer load failed: {e}"
            logger.error("Failed to load HuggingFace embedding model", model_name=model_name, error=str(e))


# -----------------------------
# Helpers
# -----------------------------

def _lc_to_openai_messages(messages: List[BaseMessage]) -> List[Dict[str, Any]]:
    """
    Convert LangChain BaseMessage objects to OpenAI-style dicts LiteLLM expects.
    Minimal mapping to avoid changing external behavior.
    """
    out: List[Dict[str, Any]] = []
    for m in messages:
        # LangChain messages often carry .type ("human","ai","system","tool") and/or .role
        role = getattr(m, "role", None)
        if not role:
            t = getattr(m, "type", None)
            role_map = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}
            role = role_map.get(t, "user")
        # content: for tools / function calls, LangChain can store dicts; LiteLLM accepts str or list[dict]
        content = getattr(m, "content", "")
        out.append({"role": role, "content": content})
    return out


def _maybe_add_web_search_options(model_name: str, kwargs: Dict[str, Any]) -> None:
    """
    Replace legacy 'tools=[{\"type\": \"web_search\"}]' with the supported web_search_options.
    We only do this when caller supplied 'tools' and the model plausibly supports search.
    This avoids changing external semantics while fixing the legacy API usage.
    """
    if not kwargs.get("tools"):
        return
    # Heuristics: models with 'search' in name (e.g., gpt-4o-search-preview, o4-mini-search),
    # or legacy openai prefixes used in config.
    name = (model_name or "").lower()
    if "search" in name or name.startswith(("gpt-", "o4-")):
        # Add LiteLLM's supported web_search_options instead of a faux tool.
        # Leave original tools as-is (the caller's tool schema).
        kwargs["web_search_options"] = kwargs.get("web_search_options") or {"search_context_size": "medium"}


# -----------------------------
# Main service
# -----------------------------

class LLMService:
    """A service class for making resilient, configuration-driven calls to LLMs."""

    def __init__(self):
        # keep existing behavior
        litellm.set_verbose = False

        # Register our structlog-backed logger using LiteLLM integration points
        # (works for both sync and async paths).
        cb = StructlogCallback()
        # Prefer the high-level callback lists so we don't alter other integrations.
        litellm.success_callback = [cb.log_success_event]
        litellm.failure_callback = [cb.log_failure_event]

        # Keep the api_key map, but don't pass None to override env
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
        """Private method to execute the LiteLLM call with retry logic."""
        try:
            return await litellm.acompletion(**litellm_kwargs)
        except ValidationError as e:
            logger.error(
                "LLM output failed Pydantic validation",
                **(litellm_kwargs.get("metadata") or {}),
                error=str(e),
            )
            raise StructuredOutputError(f"LLM output validation failed: {e}")
        except litellm.exceptions.ContentPolicyViolationError as e:
            logger.warning(
                "LLM call blocked by content policy",
                **(litellm_kwargs.get("metadata") or {}),
                error=str(e),
            )
            raise ContentFilteringError("Request was blocked by content filters.")
        except Exception as e:
            logger.error(
                "An unexpected error occurred during LLM call",
                **(litellm_kwargs.get("metadata") or {}),
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
        """Prepares the kwargs for the LiteLLM call."""
        config = self._get_config(tool_name)
        model = config.model_name
        model_provider = (model or "").split("/")[0] if model else ""
        api_key = self.api_key_map.get(model_provider, None)

        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": _lc_to_openai_messages(messages),
            "metadata": {
                "tool_name": tool_name,
                "trace_id": trace_id,
                "session_id": session_id,
            },
            **config.default_params.model_dump(),
        }
        # Do not override environment with None
        if api_key:
            kwargs["api_key"] = api_key
        if getattr(config, "api_base", None):
            kwargs["api_base"] = config.api_base
        return kwargs

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
        Replaces legacy web_search tool injection with supported web_search_options.
        """
        request_kwargs = self._prepare_request(tool_name, messages, trace_id, session_id)
        config = self._get_config(tool_name)

        # Preserve caller-supplied tools; remove the legacy "web_search" injection
        # and add LiteLLM's web_search_options if the model plausibly supports it.
        request_kwargs["tools"] = tools or []
        _maybe_add_web_search_options(config.model_name, request_kwargs)

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
        """
        Gets a response structured into a Pydantic model.
        Switch to LiteLLM's response_format parsing and return the parsed object.
        """
        request_kwargs = self._prepare_request(tool_name, messages, trace_id, session_id)
        # Use response_format=Model (LiteLLM will parse and attach .parsed)
        request_kwargs["response_format"] = response_model

        response = await self._invoke(request_kwargs)
        parsed = getattr(response.choices[0].message, "parsed", None)
        if parsed is None:
            # Defensive: if a provider didn't return parsed, try to coerce from content
            content = response.choices[0].message.content
            try:
                data = content if isinstance(content, dict) else json.loads(content or "{}")
                return response_model.model_validate(data)  # type: ignore[attr-defined]
            except Exception as e:
                raise StructuredOutputError(f"LLM did not return structured output: {e}")
        return parsed  # type: ignore[return-value]

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
        _ensure_hf_model()

        if _embed_model is None:
            logger.warn(
                "Embedding unavailable; returning empty embeddings",
                reason=_embed_import_error or "unknown",
            )
            return []

        try:
            loop = asyncio.get_running_loop()

            def _encode(texts: List[str]) -> List[List[float]]:
                vecs = _embed_model.encode(texts, normalize_embeddings=True)  # cosine-friendly
                if hasattr(vecs, "tolist"):
                    return vecs.tolist()
                return [list(vecs)] if isinstance(vecs, (list, tuple)) else []

            embeddings: List[List[float]] = await loop.run_in_executor(None, _encode, input)

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
