# backend/app/services/llm_service.py
"""
LLM Service (config-driven, resilient)

- Uses app.core.config.settings (Azure -> local YAML -> defaults)
- Secrets (API keys) are read from environment variables ONLY (never from YAML)
- Provides three stable entrypoints used across the app:
    * get_agent_response(...)           -> AIMessage (with tool_calls)
    * get_structured_response(...)      -> Pydantic model (validated)
    * get_text_response(...)            -> str
- Provides local HF embeddings for RAG (tolerant: returns [] on error)

This module keeps public signatures compatible with existing callers (graph, tools).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Type, TypeVar

import litellm
import structlog
from langchain_core.messages import AIMessage, BaseMessage
from pydantic import BaseModel, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import Settings, settings, ModelConfig  # type: ignore

# -----------------------------------------------------------------------------
# Logging / types
# -----------------------------------------------------------------------------

logger = structlog.get_logger(__name__)
PydanticModel = TypeVar("PydanticModel", bound=BaseModel)

# --- Helper env detection -----------------------------------------------------
def _is_local_env() -> bool:
    try:
        return (settings.app.environment or "local").lower() in {"local", "dev", "development"}
    except Exception:
        return False

def _mask(s: Optional[str], prefix: int = 4, suffix: int = 4) -> Optional[str]:
    if not s:
        return None
    if len(s) <= prefix + suffix:
        return s[0] + "*" * max(0, len(s) - 2) + s[-1]
    return f"{s[:prefix]}...{s[-suffix:]}"

def _exc_details() -> Dict[str, Any]:
    et, ev, _tb = sys.exc_info()
    return {"error_type": et.__name__ if et else "Unknown", "error_message": str(ev) if ev else ""}

# --- Errors ------------------------------------------------------------------
class LLMAPIError(Exception): ...
class StructuredOutputError(LLMAPIError): ...
class ContentFilteringError(LLMAPIError): ...

RETRYABLE_EXCEPTIONS = (
    litellm.exceptions.Timeout,
    litellm.exceptions.APIConnectionError,
    litellm.exceptions.RateLimitError,
    litellm.exceptions.ServiceUnavailableError,
)

# --- LiteLLM callbacks (structlog integration) --------------------------------
try:
    from litellm.integrations.custom_logger import CustomLogger
except Exception:  # pragma: no cover
    class CustomLogger:  # type: ignore
        pass

class StructlogCallback(CustomLogger):
    """Pipe LiteLLM success/failure telemetry into structlog."""

    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        metadata = kwargs.get("metadata", {}) or {}
        try:
            cost = litellm.completion_cost(completion_response=response_obj)
        except Exception:
            cost = None

        usage = getattr(response_obj, "usage", None)
        usage_dict = None
        if usage is not None:
            usage_dict = getattr(usage, "model_dump", lambda: None)() or getattr(usage, "__dict__", None)

        logger.info(
            "llm_call_success",
            model=kwargs.get("model"),
            provider=_provider(kwargs.get("model")),
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
            provider=_provider(kwargs.get("model")),
            tool_name=metadata.get("tool_name"),
            trace_id=metadata.get("trace_id"),
            duration_ms=int((end_time - start_time).total_seconds() * 1000),
            error_type=type(original_exception).__name__,
            error_message=str(original_exception),
        )

# -----------------------------------------------------------------------------
# Provider & API key resolution
# -----------------------------------------------------------------------------

def _provider(model: Optional[str]) -> str:
    """
    Heuristic: model may be "provider/model" (e.g., "openai/gpt-4o-mini").
    If no explicit provider, assume "openai".
    """
    if not model:
        return "openai"
    if "/" in model:
        return model.split("/", 1)[0].strip().lower()
    # simple heuristics for common prefixes
    if model.startswith(("gpt-", "gpt4", "gpt-4")):
        return "openai"
    if model.startswith(("claude",)):
        return "anthropic"
    return "openai"

def _env_api_key_for(provider: str) -> Optional[str]:
    """
    Map providers to env var names. Keep additive and backward compatible.
    """
    env_map = {
        "openai": os.getenv("OPENAI_API_KEY"),
        "azure": os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY"),
        "anthropic": os.getenv("ANTHROPIC_API_KEY"),
        "groq": os.getenv("GROQ_API_KEY"),
        "cohere": os.getenv("COHERE_API_KEY"),
    }
    return env_map.get(provider)

# -----------------------------------------------------------------------------
# Embeddings (local, tolerant)
# -----------------------------------------------------------------------------

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
    logger.debug("Embedding config", model_name=model_name, dim=dim, distance=distance, column=column)
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
            logger.warning("Embedding disabled: sentence-transformers not available", error=str(e))
            return
        cfg = _get_embedding_config()
        try:
            _embed_model = SentenceTransformer(cfg["model_name"], device="cpu")
            logger.info("HF embedding model loaded", model_name=cfg["model_name"])
        except Exception as e:  # pragma: no cover
            _embed_import_error = f"SentenceTransformer load failed: {e}"
            logger.error("Failed loading HF embedding model", model_name=cfg["model_name"], error=str(e))

# -----------------------------------------------------------------------------
# Message conversion
# -----------------------------------------------------------------------------

def _lc_to_openai_messages(messages: List[BaseMessage]) -> List[Dict[str, Any]]:
    """
    Convert LangChain BaseMessage objects to OpenAI-style dicts for LiteLLM.
    """
    out: List[Dict[str, Any]] = []
    for m in messages:
        role = getattr(m, "role", None) or {"human": "user", "ai": "assistant"}.get(getattr(m, "type", ""), "user")
        out.append({"role": role, "content": getattr(m, "content", "")})
    logger.debug("Converted LC messages", count=len(messages))
    return out

# -----------------------------------------------------------------------------
# LLM Service
# -----------------------------------------------------------------------------

class LLMService:
    """
    Unified LLM interface:
      - Provider and model pulled from settings.llm_tools[tool_name] (Azure/YAML/defaults)
      - API key resolved from environment based on provider
      - Retries and structured parsing via LiteLLM
    """

    def __init__(self):
        # Verbose SDK logging in local/dev
        if _is_local_env():
            os.environ.setdefault("OPENAI_LOG", "debug")
            os.environ.setdefault("LITELLM_LOG", "DEBUG")
        cb = StructlogCallback()
        litellm.success_callback = [cb.log_success_event]
        litellm.failure_callback = [cb.log_failure_event]

        # Log available providers/keys presence
        key_map = {p: bool(_env_api_key_for(p)) for p in ["openai", "anthropic", "groq", "cohere", "azure"]}
        logger.info("LLMService initialized", env=settings.app.environment, api_keys_present=key_map)

    # ------------------- request preparation -------------------

    def _tool_cfg(self, tool_name: str) -> ModelConfig:
        cfg = settings.llm_tools.get(tool_name)
        if cfg is None:
            # Soft fallback: pick a reasonable default if the exact tool isn't configured
            # (keeps behavior non-breaking for missing keys)
            fallback = settings.llm_tools.get("question_generator") or next(iter(settings.llm_tools.values()))
            logger.warning("LLM tool config not found; using fallback", tool_name=tool_name)
            return fallback
        return cfg

    def _prepare_request(
        self,
        tool_name: str,
        messages: List[BaseMessage],
        trace_id: Optional[str],
        session_id: Optional[str],
    ) -> Dict[str, Any]:
        cfg = self._tool_cfg(tool_name)
        model_name = cfg.model
        provider = _provider(model_name)
        api_key = _env_api_key_for(provider)

        kwargs: Dict[str, Any] = {
            "model": model_name,
            "messages": _lc_to_openai_messages(messages),
            "metadata": {"tool_name": tool_name, "trace_id": trace_id, "session_id": session_id},
            "temperature": cfg.temperature,
            "max_tokens": cfg.max_output_tokens,       # LiteLLM expects 'max_tokens'
            "timeout": cfg.timeout_s,                  # LiteLLM uses 'timeout' seconds
        }
        if api_key:
            kwargs["api_key"] = api_key
        else:
            logger.warning("No API key for provider; call may fail", provider=provider, tool_name=tool_name)

        logger.debug(
            "LLM request prepared",
            tool_name=tool_name, model=model_name, provider=provider,
            timeout_s=cfg.timeout_s, max_tokens=cfg.max_output_tokens, temperature=cfg.temperature,
        )
        return kwargs

    # ------------------- core invoke with retries -------------------

    @retry(
        wait=wait_exponential(multiplier=1, min=1, max=30),
        stop=stop_after_attempt(max(1, settings.agent.max_retries)),
        retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
        reraise=True,
    )
    async def _invoke(self, litellm_kwargs: Dict[str, Any]) -> litellm.ModelResponse:
        t0 = time.perf_counter()
        try:
            resp = await litellm.acompletion(**litellm_kwargs)
            logger.debug("LLM call ok", duration_ms=round((time.perf_counter() - t0) * 1000, 1))
            return resp
        except ValidationError as e:
            logger.error("Pydantic validation during LLM call", error=str(e), exc_info=True)
            raise StructuredOutputError(f"LLM output validation failed: {e}")
        except litellm.exceptions.ContentPolicyViolationError as e:
            logger.warning("LLM content policy block", error=str(e))
            raise ContentFilteringError("Request was blocked by content filters.")
        except Exception as e:
            details = _exc_details()
            logger.error("LLM call unexpected error", error=str(e), **details, exc_info=True)
            raise LLMAPIError(f"Unexpected LLM API error: {e}")

    # ------------------- public entrypoints -------------------

    async def get_agent_response(
        self,
        tool_name: str,
        messages: List[BaseMessage],
        tools: List[Dict[str, Any]],
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> AIMessage:
        """
        Planner-style response that may include tool calls.
        """
        req = self._prepare_request(tool_name, messages, trace_id, session_id)
        req["tools"] = tools or []
        response = await self._invoke(req)
        msg = response.choices[0].message

        # Extract tool calls robustly
        tool_calls = []
        if getattr(msg, "tool_calls", None):
            try:
                tool_calls = [
                    {"id": c.id, "name": c.function.name, "args": json.loads(c.function.arguments)}
                    for c in msg.tool_calls
                ]
            except Exception as e:
                logger.warning("Failed to parse tool_calls", error=str(e))

        logger.debug("Agent response parsed", has_tool_calls=bool(tool_calls))
        return AIMessage(content=msg.content or "", tool_calls=tool_calls)

    async def get_structured_response(
        self,
        tool_name: str,
        messages: List[BaseMessage],
        response_model: Type[PydanticModel],
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> PydanticModel:
        """
        Request response parsed directly into a Pydantic model.
        Uses LiteLLM's Pydantic parsing when available; falls back to JSON parsing.
        """
        req = self._prepare_request(tool_name, messages, trace_id, session_id)
        req["response_format"] = response_model  # LiteLLM Pydantic parsing

        response = await self._invoke(req)
        parsed = getattr(response.choices[0].message, "parsed", None)
        if parsed is not None:
            return parsed  # type: ignore[return-value]

        # Fallback: parse JSON content manually
        content = response.choices[0].message.content
        logger.debug("No parsed object; trying manual JSON parse", has_content=bool(content))
        try:
            data = content if isinstance(content, dict) else json.loads(content or "{}")
            return response_model.model_validate(data)  # type: ignore[attr-defined]
        except Exception as e:
            logger.error("Structured parse failed", tool_name=tool_name, error=str(e), exc_info=True)
            raise StructuredOutputError(f"LLM did not return structured output: {e}")

    async def get_text_response(
        self,
        tool_name: str,
        messages: List[BaseMessage],
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> str:
        req = self._prepare_request(tool_name, messages, trace_id, session_id)
        response = await self._invoke(req)
        content = response.choices[0].message.content
        return content if isinstance(content, str) else (content or "")

    async def get_embedding(self, input: List[str], model: Optional[str] = None) -> List[List[float]]:
        """
        Local HF embeddings. Returns [] on any failure.
        """
        if not input:
            return []
        _ensure_hf_model()
        if _embed_model is None:
            logger.warning("Embeddings unavailable", reason=_embed_import_error or "unknown")
            return []
        try:
            loop = asyncio.get_running_loop()

            def _encode(texts: List[str]) -> List[List[float]]:
                vecs = _embed_model.encode(texts, normalize_embeddings=True)  # type: ignore[attr-defined]
                return vecs.tolist() if hasattr(vecs, "tolist") else [list(vecs)]

            t0 = time.perf_counter()
            out = await loop.run_in_executor(None, _encode, input)
            logger.debug("Embeddings ok", vectors=len(out), duration_ms=round((time.perf_counter() - t0) * 1000, 1))
            cfg = _get_embedding_config()
            if out and len(out[0]) != int(cfg["dim"]):
                logger.warning("Embedding dim mismatch", got=len(out[0]), expected=int(cfg["dim"]), column=cfg["column"])
            return out
        except Exception as e:
            logger.error("Embeddings failed", error=str(e), exc_info=True)
            return []


# Singleton
def get_llm_service() -> LLMService:
    return LLMService()

llm_service = get_llm_service()
