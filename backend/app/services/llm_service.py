# backend/app/services/llm_service.py
# LLM Service (config-driven, resilient)
#
# - Uses app.core.config.settings (Azure -> local YAML -> defaults)
# - Secrets (API keys) are read from environment variables ONLY (never from YAML)
# - Provides three stable entrypoints used across the app:
#     * get_agent_response(...)           -> AIMessage (with tool_calls)
#     * get_structured_response(...)      -> Pydantic model (validated)
#     * get_text_response(...)            -> str
# - Provides local HF embeddings for RAG (tolerant: returns [] on error)
#
# This module keeps public signatures compatible with existing callers (graph, tools).

from __future__ import annotations

import os
import sys
import re
import json
import time
import asyncio
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Type, TypeVar

# --- Silence verbose third-party logging BEFORE importing them ----------------
# Keep errors, drop info/debug.
os.environ.setdefault("LITELLM_LOG", "WARNING")
os.environ.setdefault("LITELLM_VERBOSE", "0")
os.environ.setdefault("LITELLM_DEBUG", "0")
os.environ.setdefault("OPENAI_LOG", "error")  # avoid OpenAI SDK verbose logs

import logging

def _silence_external_loggers() -> None:
    targets = [
        "LiteLLM",
        "litellm",
        "litellm.utils",
        "litellm.router",
        "litellm.proxy",
        "litellm.proxy.proxy_server",
        "litellm.litellm_core_utils.litellm_logging",
        "litellm.litellm_core_utils.callback_utils",
        "httpx",
    ]
    for name in targets:
        lg = logging.getLogger(name)
        lg.setLevel(logging.WARNING)
        lg.propagate = False
        if not lg.handlers:
            lg.addHandler(logging.NullHandler())

_silence_external_loggers()

import litellm
import structlog
from pydantic import BaseModel, ValidationError
from langchain_core.messages import AIMessage, BaseMessage
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import Settings, settings, ModelConfig  # type: ignore
from app.agent.schemas import schema_for  # optional helper (not required by callers)

# -----------------------------------------------------------------------------
# Logging / types
# -----------------------------------------------------------------------------

logger = structlog.get_logger(__name__)
PydanticModel = TypeVar("PydanticModel", bound=BaseModel)


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _truncate(s: Any, n: int = 4000) -> str:
    txt = "" if s is None else str(s)
    return txt if len(txt) <= n else (txt[:n] + "…")


def _compact_messages_for_log(msgs: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for m in msgs or []:
        role = str(m.get("role") or "user")
        c = m.get("content")
        if isinstance(c, (dict, list)):
            try:
                c = json.dumps(c, ensure_ascii=False)
            except Exception:
                c = str(c)
        out.append({"role": role, "content": _truncate(c, 1200)})
    return out


def set_llm_service(new_service) -> None:
    global llm_service
    llm_service = new_service


# --- Errors ------------------------------------------------------------------
class LLMAPIError(Exception):
    ...


class StructuredOutputError(LLMAPIError):
    ...


class ContentFilteringError(LLMAPIError):
    ...


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
    """Pipe LiteLLM success/failure telemetry into structlog.

    We keep this as no-op for success to avoid duplicate happy-path logs.
    Errors are logged in _invoke().
    """

    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        # no-op: happy path is logged explicitly by our service
        return

    def log_failure_event(self, kwargs, original_exception, start_time, end_time):
        # no-op here; _invoke handles error logging centrally
        return


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
    # no happy-path logs here
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
            logger.error("Embedding disabled: sentence-transformers not available", error=str(e))
            return
        cfg = _get_embedding_config()
        try:
            _embed_model = SentenceTransformer(cfg["model_name"], device="cpu")
        except Exception as e:  # pragma: no cover
            _embed_import_error = f"SentenceTransformer load failed: {e}"
            logger.error("Failed loading HF embedding model", model_name=cfg["model_name"], error=str(e))


# -----------------------------------------------------------------------------
# JSON normalization (fix: stop double-encoded / fenced JSON)
# -----------------------------------------------------------------------------

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def coerce_json(content: Any) -> Any:
    """
    Normalize possible JSON outputs from LLMs:

    - If dict/list: return as-is.
    - If non-string: return as-is (callers may handle objects).
    - If string:
        * Strip ```json ... ``` fences (and generic ``` ... ```).
        * json.loads once (no double-dumps).
        * On parse failure, wrap as {"text": <string>} so callers never get raw JSON strings.
    """
    if isinstance(content, (dict, list)):
        return content
    if not isinstance(content, str):
        return content

    s = content.strip()
    m = _JSON_FENCE_RE.match(s)
    if m:
        s = m.group(1).strip()

    try:
        return json.loads(s)
    except Exception:
        return {"text": s}


# -----------------------------------------------------------------------------
# Message conversion
# -----------------------------------------------------------------------------

def _lc_to_openai_messages(messages: List[BaseMessage]) -> List[Dict[str, Any]]:
    """
    Convert LangChain BaseMessage objects to OpenAI-style dicts for LiteLLM.
    Make a best-effort to preserve dict/list content (e.g., multimodal).
    """
    out: List[Dict[str, Any]] = []
    for m in messages:
        role = getattr(m, "role", None)
        if not role:
            t = getattr(m, "type", "")
            role = {"human": "user", "ai": "assistant", "system": "system"}.get(t, "user")
        content = getattr(m, "content", "")
        if isinstance(content, (dict, list)):
            out.append({"role": role, "content": content})
        else:
            out.append({"role": role, "content": str(content) if content is not None else ""})
    return out


# -----------------------------------------------------------------------------
# Helpers (response model checks)
# -----------------------------------------------------------------------------

def _is_pydantic_cls(x: Any) -> bool:
    try:
        return isinstance(x, type) and issubclass(x, BaseModel)
    except Exception:
        return False


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
        # Disable LiteLLM verbosity regardless of environment (keep errors only)
        _silence_external_loggers()

        # Disable LiteLLM debug logger / background workers
        os.environ["LITELLM_LOG"] = "WARNING"
        os.environ["LITELLM_DEBUG"] = "0"
        os.environ["LITELLM_DISABLE_BACKGROUND_WORKER"] = "1"

        cb = StructlogCallback()
        litellm.success_callback = [cb.log_success_event]  # no-op
        litellm.failure_callback = [cb.log_failure_event]  # no-op

    # ------------------- request preparation -------------------

    def _tool_cfg(self, tool_name: str) -> ModelConfig:
        cfg = settings.llm_tools.get(tool_name)
        if cfg is None:
            fallback = settings.llm_tools.get("question_generator") or next(iter(settings.llm_tools.values()))
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
        provider_name = _provider(model_name)
        api_key = _env_api_key_for(provider_name)

        kwargs: Dict[str, Any] = {
            "model": model_name,
            "messages": _lc_to_openai_messages(messages),
            "metadata": {"tool_name": tool_name, "trace_id": trace_id, "session_id": session_id},
            "temperature": cfg.temperature,
            "max_tokens": cfg.max_output_tokens,
            "timeout": cfg.timeout_s,
        }
        if api_key:
            kwargs["api_key"] = api_key
        else:
            logger.error("No API key for provider; call may fail", provider=provider_name, tool_name=tool_name)
        return kwargs

    # ------------------- core invoke with retries -------------------

    @retry(
        wait=wait_exponential(multiplier=1, min=1, max=30),
        stop=stop_after_attempt(max(1, settings.agent.max_retries)),
        retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
        reraise=True,
    )
    async def _invoke(self, litellm_kwargs: Dict[str, Any]) -> litellm.ModelResponse:
        try:
            resp = await litellm.acompletion(**litellm_kwargs)
            return resp
        except ValidationError as e:
            logger.error("Pydantic validation during LLM call", error=str(e), exc_info=True)
            raise StructuredOutputError(f"LLM output validation failed: {e}")
        except litellm.exceptions.ContentPolicyViolationError as e:
            logger.error("LLM content policy block", error=str(e))
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
        if tools:
            req["tool_choice"] = "auto"

        # PROMPT log
        logger.info(
            "llm_prompt",
            sent_at=_now_iso(),
            tool_name=tool_name,
            model=req.get("model"),
            messages=_compact_messages_for_log(req.get("messages", [])),
        )

        response = await self._invoke(req)
        msg = response.choices[0].message

        tool_calls = []
        if getattr(msg, "tool_calls", None):
            try:
                tool_calls = [
                    {
                        "id": c.id,
                        "name": c.function.name,
                        "args": coerce_json(c.function.arguments),
                    }
                    for c in msg.tool_calls
                ]
            except Exception as e:
                logger.error("Failed to parse tool_calls", error=str(e), exc_info=True)

        # RESPONSE log
        logger.info(
            "llm_response",
            received_at=_now_iso(),
            tool_name=tool_name,
            model=req.get("model"),
            text=_truncate(msg.content or "", 4000),
        )

        return AIMessage(content=msg.content or "", tool_calls=tool_calls)

    async def get_structured_response(
        self,
        tool_name: str,
        messages: List[BaseMessage],
        response_model: Type[PydanticModel],
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
        as_dict: bool = False,  # accepted for compatibility; ignored
    ) -> PydanticModel:
        """
        Request response parsed directly into a Pydantic model.
        """
        if not _is_pydantic_cls(response_model):
            raise TypeError(
                f"response_model must be a Pydantic BaseModel subclass; got: {response_model!r}. "
                "Use a Pydantic model for structured outputs."
            )

        req = self._prepare_request(tool_name, messages, trace_id, session_id)
        req["response_format"] = response_model

        # PROMPT log
        logger.info(
            "llm_prompt",
            sent_at=_now_iso(),
            tool_name=tool_name,
            model=req.get("model"),
            messages=_compact_messages_for_log(req.get("messages", [])),
        )

        response = await self._invoke(req)

        # Handle possible refusal from provider
        refusal = getattr(response, "refusal", None)
        if not refusal:
            msg0 = getattr(response, "choices", [None])[0]
            msg0 = getattr(msg0, "message", None)
            refusal = getattr(msg0, "refusal", None) if msg0 else None
        if refusal:
            txt = getattr(refusal, "message", None) or str(refusal)
            logger.error("llm_structured_refusal", tool_name=tool_name, refusal=str(txt))
            raise StructuredOutputError(f"Provider refused structured output: {txt}")

        parsed = getattr(response.choices[0].message, "parsed", None)
        if parsed is not None:
            try:
                if isinstance(parsed, BaseModel):
                    data = parsed.model_dump()
                else:
                    data = parsed
                out = response_model.model_validate(data)  # type: ignore[attr-defined]
            except AttributeError:
                data = parsed.model_dump() if isinstance(parsed, BaseModel) else parsed
                out = response_model(**data)  # type: ignore[call-arg]

            # RESPONSE log
            try:
                payload = out.model_dump_json()  # pydantic v2
            except Exception:
                try:
                    payload = json.dumps(out.model_dump(), ensure_ascii=False)
                except Exception:
                    payload = str(out)
            logger.info(
                "llm_response",
                received_at=_now_iso(),
                tool_name=tool_name,
                model=req.get("model"),
                json=_truncate(payload, 4000),
            )
            return out

        # Fallback: parse from content
        content = response.choices[0].message.content
        try:
            data = coerce_json(content)
            out = response_model.model_validate(data)  # type: ignore[attr-defined]
            logger.info(
                "llm_response",
                received_at=_now_iso(),
                tool_name=tool_name,
                model=req.get("model"),
                json=_truncate(json.dumps(data, ensure_ascii=False), 4000),
            )
            return out
        except AttributeError:
            out = response_model(**data)  # type: ignore[name-defined]
            try:
                payload = out.json()
            except Exception:
                try:
                    payload = json.dumps(out.dict(), ensure_ascii=False)  # type: ignore[attr-defined]
                except Exception:
                    payload = str(out)
            logger.info(
                "llm_response",
                received_at=_now_iso(),
                tool_name=tool_name,
                model=req.get("model"),
                json=_truncate(payload, 4000),
            )
            return out
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

        # PROMPT log
        logger.info(
            "llm_prompt",
            sent_at=_now_iso(),
            tool_name=tool_name,
            model=req.get("model"),
            messages=_compact_messages_for_log(req.get("messages", [])),
        )

        response = await self._invoke(req)
        content = response.choices[0].message.content
        text = content if isinstance(content, str) else (content or "")

        # RESPONSE log
        logger.info(
            "llm_response",
            received_at=_now_iso(),
            tool_name=tool_name,
            model=req.get("model"),
            text=_truncate(text, 4000),
        )
        return text

    async def get_embedding(self, input: List[str], model: Optional[str] = None) -> List[List[float]]:
        """
        Local HF embeddings. Returns [] on any failure.
        """
        if not input:
            return []
        _ensure_hf_model()
        if _embed_model is None:
            logger.error("Embeddings unavailable", reason=_embed_import_error or "unknown")
            return []
        try:
            loop = asyncio.get_running_loop()

            def _encode(texts: List[str]) -> List[List[float]]:
                vecs = _embed_model.encode(texts, normalize_embeddings=True)  # type: ignore[attr-defined]
                return vecs.tolist() if hasattr(vecs, "tolist") else [list(vecs)]

            out = await loop.run_in_executor(None, _encode, input)
            return out
        except Exception as e:
            logger.error("Embeddings failed", error=str(e), exc_info=True)
            return []


# Singleton
def get_llm_service() -> LLMService:
    return LLMService()


llm_service = get_llm_service()
