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
import time
import sys
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


def _is_local_env() -> bool:
    try:
        return (settings.APP_ENVIRONMENT or "local").lower() in {"local", "dev", "development"}
    except Exception:
        return False


def _mask(s: Optional[str], prefix: int = 4, suffix: int = 4) -> Optional[str]:
    if not s:
        return None
    if len(s) <= prefix + suffix:
        return s[0] + "*" * max(0, len(s) - 2) + s[-1]
    return f"{s[:prefix]}...{s[-suffix:]}"


def _exc_details() -> Dict[str, Any]:
    et, ev, tb = sys.exc_info()
    return {
        "error_type": et.__name__ if et else "Unknown",
        "error_message": str(ev) if ev else "",
    }


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

# ---- LiteLLM callbacks (CustomLogger + completion_cost) ---------------------

try:
    from litellm.integrations.custom_logger import CustomLogger
except Exception:  # pragma: no cover
    class CustomLogger:  # type: ignore
        pass


class StructlogCallback(CustomLogger):
    """Integrates LiteLLM logging with structlog for unified observability."""

    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        metadata = kwargs.get("metadata", {}) or {}
        try:
            cost = litellm.completion_cost(completion_response=response_obj)
        except Exception:
            cost = None

        usage = getattr(response_obj, "usage", None)
        usage_dict = (
            usage.model_dump() if hasattr(usage, "model_dump")
            else getattr(usage, "__dict__", None)
        ) if usage is not None else None

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
_embed_model = None
_embed_lock = threading.Lock()
_embed_import_error: Optional[str] = None


def _get_embedding_config() -> Dict[str, Any]:
    model_name = os.getenv("EMBEDDING__MODEL_NAME", "sentence-transformers/all-MiniLM-L6-v2")
    dim_str = os.getenv("EMBEDDING__DIM", "384")
    distance = os.getenv("EMBEDDING__DISTANCE_METRIC", "cosine")
    column = os.getenv("EMBEDDING__COLUMN", "synopsis_embedding")
    try:
        dim = int(dim_str)
    except Exception:
        dim = 384

    logger.debug(
        "Embedding configuration loaded",
        model_name=model_name,
        dim=dim,
        distance=distance,
        column=column,
    )
    return {"model_name": model_name, "dim": dim, "distance": distance, "column": column}


def _ensure_hf_model():
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
        try:
            _embed_model = SentenceTransformer(cfg["model_name"], device="cpu")
            logger.info("HuggingFace embedding model loaded", model_name=cfg["model_name"])
        except Exception as e:  # pragma: no cover
            _embed_import_error = f"SentenceTransformer load failed: {e}"
            logger.error("Failed to load HuggingFace embedding model", model_name=cfg["model_name"], error=str(e))


# -----------------------------
# Helpers
# -----------------------------

def _lc_to_openai_messages(messages: List[BaseMessage]) -> List[Dict[str, Any]]:
    """
    Convert LangChain BaseMessage objects to OpenAI-style dicts LiteLLM expects.
    """
    out: List[Dict[str, Any]] = []
    for m in messages:
        role = getattr(m, "role", None)
        if not role:
            t = getattr(m, "type", None)
            role_map = {"human": "user", "ai": "assistant", "system": "system", "tool": "tool"}
            role = role_map.get(t, "user")
        content = getattr(m, "content", "")
        out.append({"role": role, "content": content})
    try:
        logger.debug("Converted LC messages", count=len(messages), roles=[d.get("role") for d in out])
    except Exception:
        logger.debug("Converted LC messages", count=len(messages))
    return out


# -----------------------------
# Main service
# -----------------------------

class LLMService:
    """A service class for making resilient, configuration-driven calls to LLMs."""

    def __init__(self):
        # Keep existing setting; add environment-driven verbose controls
        litellm.set_verbose = False

        # Local/dev: make OpenAI & LiteLLM chatty (SDK-level logging)
        if _is_local_env():
            # Only set if not already explicitly configured by user
            if not os.environ.get("OPENAI_LOG"):
                os.environ["OPENAI_LOG"] = "debug"
                logger.debug("Set OPENAI_LOG=debug for local verbose SDK logging")
            if not os.environ.get("LITELLM_LOG"):
                os.environ["LITELLM_LOG"] = "DEBUG"
                logger.debug("Set LITELLM_LOG=DEBUG for local LiteLLM logging")

        cb = StructlogCallback()
        litellm.success_callback = [cb.log_success_event]
        litellm.failure_callback = [cb.log_failure_event]

        # API keys (masked in logs)
        self.api_key_map = {
            "groq": settings.GROQ_API_KEY.get_secret_value() if getattr(settings, "GROQ_API_KEY", None) else None,
            "openai": settings.OPENAI_API_KEY.get_secret_value() if getattr(settings, "OPENAI_API_KEY", None) else None,
        }
        try:
            logger.info(
                "LLMService initialized",
                env=settings.APP_ENVIRONMENT,
                openai_key_present=bool(self.api_key_map.get("openai")),
                openai_key_mask=_mask(self.api_key_map.get("openai")),
                groq_key_present=bool(self.api_key_map.get("groq")),
                groq_key_mask=_mask(self.api_key_map.get("groq")),
                default_llm_model=getattr(settings, "default_llm_model", None),
                llm_tool_models={k: v.model_name for k, v in (settings.llm_tools or {}).items()},
                prompt_keys=list((settings.llm_prompts or {}).keys()),
            )
        except Exception:
            # Never fail init due to logging
            logger.debug("LLMService init logging skipped due to unexpected error")

    def _get_config(self, tool_name: str) -> LLMToolSetting:
        cfg = settings.llm_tools.get(tool_name, settings.llm_tools["default"])
        try:
            logger.debug(
                "Resolved tool configuration",
                tool_name=tool_name,
                model_name=getattr(cfg, "model_name", None),
                api_base=getattr(cfg, "api_base", None),
                has_default_params=bool(getattr(cfg, "default_params", None)),
            )
        except Exception:
            logger.debug("Resolved tool configuration", tool_name=tool_name)
        return cfg

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(settings.agent.max_retries),
        retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
        reraise=True,
    )
    async def _invoke(self, litellm_kwargs: Dict[str, Any]) -> litellm.ModelResponse:
        # Pre-call diagnostics (no secrets)
        metadata = litellm_kwargs.get("metadata", {}) or {}
        model = litellm_kwargs.get("model")
        provider = (model or "").split("/")[0] if model else ""
        msg_count = len(litellm_kwargs.get("messages") or [])
        has_tools = bool(litellm_kwargs.get("tools"))
        has_api_key = bool(litellm_kwargs.get("api_key"))
        timeout_s = litellm_kwargs.get("timeout", None)

        logger.debug(
            "Invoking LLM",
            model=model,
            provider=provider,
            tool_name=metadata.get("tool_name"),
            trace_id=metadata.get("trace_id"),
            session_id=metadata.get("session_id"),
            message_count=msg_count,
            has_tools=has_tools,
            has_api_key=has_api_key,
            timeout_seconds=timeout_s,
        )

        t0 = time.perf_counter()
        try:
            response = await litellm.acompletion(**litellm_kwargs)
            dt_ms = round((time.perf_counter() - t0) * 1000, 1)
            try:
                content = response.choices[0].message.content
                tool_calls = getattr(response.choices[0].message, "tool_calls", None)
                logger.debug(
                    "LLM invocation successful",
                    duration_ms=dt_ms,
                    has_content=bool(content),
                    has_tool_calls=bool(tool_calls),
                    usage=getattr(response, "usage", None).__dict__ if getattr(response, "usage", None) else None,
                )
            except Exception:
                logger.debug("LLM invocation successful", duration_ms=dt_ms)
            return response

        except ValidationError as e:
            logger.error(
                "LLM output failed Pydantic validation",
                **metadata,
                error=str(e),
                exc_info=True,
            )
            raise StructuredOutputError(f"LLM output validation failed: {e}")

        except litellm.exceptions.ContentPolicyViolationError as e:
            logger.warning(
                "LLM call blocked by content policy",
                **metadata,
                error=str(e),
            )
            raise ContentFilteringError("Request was blocked by content filters.")

        except Exception as e:
            details = _exc_details()
            # Add special hint if this looks like an auth issue but keep behavior identical
            auth_hint = isinstance(e, getattr(litellm.exceptions, "AuthenticationError", tuple())) or "invalid_api_key" in str(e).lower()
            logger.error(
                "An unexpected error occurred during LLM call",
                **metadata,
                model=model,
                provider=provider,
                error=str(e),
                auth_suspected=bool(auth_hint),
                **details,
                exc_info=True,
            )
            raise LLMAPIError(f"An unexpected API error occurred: {e}")

    def _prepare_request(
        self,
        tool_name: str,
        messages: List[BaseMessage],
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        config = self._get_config(tool_name)
        model = config.model_name
        model_provider = (model or "").split("/")[0] if model else ""
        api_key = self.api_key_map.get(model_provider, None)

        try:
            logger.debug(
                "Preparing LLM request",
                tool_name=tool_name,
                model=model,
                model_provider=model_provider,
                has_api_key=bool(api_key),
                message_count=len(messages),
                trace_id=trace_id,
                api_base=getattr(config, "api_base", None),
                default_params=getattr(config, "default_params", None).model_dump() if getattr(config, "default_params", None) else {},
            )
        except Exception:
            logger.debug(
                "Preparing LLM request",
                tool_name=tool_name,
                model=model,
                model_provider=model_provider,
                has_api_key=bool(api_key),
                message_count=len(messages),
                trace_id=trace_id,
            )

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
        if api_key:
            kwargs["api_key"] = api_key
            logger.debug("API key attached to request", provider=model_provider)
        else:
            logger.warning("No API key available for provider", provider=model_provider, tool_name=tool_name)
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
        NOTE: web search is now a separate tool (see data_tools.web_search).
        """
        request_kwargs = self._prepare_request(tool_name, messages, trace_id, session_id)
        request_kwargs["tools"] = tools or []
        logger.debug(
            "Agent response request prepared",
            tool_name=tool_name,
            trace_id=trace_id,
            session_id=session_id,
            tools_count=len(tools or []),
        )

        response = await self._invoke(request_kwargs)
        response_message = response.choices[0].message

        tool_calls = []
        if getattr(response_message, "tool_calls", None):
            try:
                tool_calls = [
                    {"id": call.id, "name": call.function.name, "args": json.loads(call.function.arguments)}
                    for call in response_message.tool_calls
                ]
            except Exception as e:
                logger.warning("Failed to parse tool_calls from response", error=str(e))

        logger.debug(
            "Agent response received",
            has_content=bool(response_message.content),
            tool_calls_count=len(tool_calls),
        )
        return AIMessage(content=response_message.content or "", tool_calls=tool_calls)

    async def get_structured_response(
        self,
        tool_name: str,
        messages: List[BaseMessage],
        response_model: Type[PydanticModel],
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> PydanticModel:
        request_kwargs = self._prepare_request(tool_name, messages, trace_id, session_id)
        request_kwargs["response_format"] = response_model
        logger.debug(
            "Structured response request prepared",
            tool_name=tool_name,
            response_model=getattr(response_model, "__name__", str(response_model)),
        )

        response = await self._invoke(request_kwargs)
        parsed = getattr(response.choices[0].message, "parsed", None)
        if parsed is None:
            content = response.choices[0].message.content
            logger.debug("No parsed object on response; attempting manual parse", has_content=bool(content))
            try:
                data = content if isinstance(content, dict) else json.loads(content or "{}")
                result = response_model.model_validate(data)  # type: ignore[attr-defined]
                logger.debug("Manual parse to response_model succeeded")
                return result
            except Exception as e:
                logger.error(
                    "Structured output missing/invalid",
                    tool_name=tool_name,
                    error=str(e),
                    exc_info=True,
                )
                raise StructuredOutputError(f"LLM did not return structured output: {e}")
        logger.debug("Structured response parsed via SDK attribute")
        return parsed  # type: ignore[return-value]

    async def get_text_response(
        self,
        tool_name: str,
        messages: List[BaseMessage],
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> str:
        request_kwargs = self._prepare_request(tool_name, messages, trace_id, session_id)
        logger.debug("Text response request prepared", tool_name=tool_name)

        response = await self._invoke(request_kwargs)
        content = response.choices[0].message.content
        logger.debug("Text response received", has_content=bool(content))
        return content if isinstance(content, str) else ""

    async def get_embedding(self, input: List[str], model: str | None = None) -> List[List[float]]:
        """
        Generates embeddings using a local HuggingFace model.
        Non-blocking: returns [] on any error.
        """
        if not input:
            logger.debug("Embedding called with empty input; returning []")
            return []

        cfg = _get_embedding_config()
        _ensure_hf_model()

        if _embed_model is None:
            logger.warn("Embedding unavailable; returning empty embeddings", reason=_embed_import_error or "unknown")
            return []

        try:
            loop = asyncio.get_running_loop()

            def _encode(texts: List[str]) -> List[List[float]]:
                vecs = _embed_model.encode(texts, normalize_embeddings=True)
                if hasattr(vecs, "tolist"):
                    return vecs.tolist()
                return [list(vecs)] if isinstance(vecs, (list, tuple)) else []

            logger.debug("Starting embedding encode", batch_size=len(input), model_name=cfg["model_name"])
            t0 = time.perf_counter()
            embeddings: List[List[float]] = await loop.run_in_executor(None, _encode, input)
            dt_ms = round((time.perf_counter() - t0) * 1000, 1)
            logger.debug("Embedding encode completed", vectors=len(embeddings), duration_ms=dt_ms)

            if embeddings and len(embeddings[0]) != int(cfg["dim"]):
                logger.warn(
                    "Embedding dimension differs from configured dim; consider aligning model and DB column",
                    got=len(embeddings[0]), expected=int(cfg["dim"]), column=cfg["column"], model_name=cfg["model_name"],
                )
            return embeddings
        except Exception as e:
            logger.error("Embedding generation failed", error=str(e), exc_info=True)
            return []


def get_llm_service() -> LLMService:
    """Returns a singleton instance of the LLMService."""
    return LLMService()


llm_service = get_llm_service()
