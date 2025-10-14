# backend/app/services/llm_service.py
# LLM Service (config-driven, resilient)
#
# - Pulls models/temps/tokens/timeouts from app.core.config.settings
# - Secrets (API keys) ONLY from environment (never from YAML)
# - Three stable entrypoints used across the app:
#     * get_agent_response(...)      -> AIMessage (may include tool_calls)
#     * get_structured_response(...) -> Pydantic model (validated)
#     * get_text_response(...)       -> str
# - Tolerant local HF embeddings for RAG (returns [] on failure)
#
# Major changes in this rewrite:
# - Robust message coercion: accepts LangChain BaseMessage OR raw {role, content} dicts.
# - Strips blank messages and injects a safe system message if prompt would be empty.
# - Normalizes fenced JSON and double-encoded JSON via coerce_json.
# - Structured output path handles array-root schemas by WRAPPING them into an object
#   (OpenAI requires root type=object) and UNWRAPS after parsing.
# - Structured output path prefers provider-parsed .parsed, falls back to content->JSON.
# - Logging trims payloads, masks secrets, and records per-tool metadata.
# - Retry policy consolidated with clear, typed exceptions.

from __future__ import annotations

import os
import sys
import re
import json
import asyncio
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, TypeVar, Union

# --- Silence verbose third-party logging BEFORE importing them ----------------
os.environ.setdefault("LITELLM_LOG", "WARNING")
os.environ.setdefault("LITELLM_VERBOSE", "0")
os.environ.setdefault("LITELLM_DEBUG", "0")
os.environ.setdefault("OPENAI_LOG", "error")  # keep OpenAI SDK quiet

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
from pydantic.type_adapter import TypeAdapter
from langchain_core.messages import AIMessage, BaseMessage
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import settings, ModelConfig  # type: ignore

# -----------------------------------------------------------------------------
# Logging / types
# -----------------------------------------------------------------------------

logger = structlog.get_logger(__name__)
PydanticModel = TypeVar("PydanticModel", bound=BaseModel)

# -----------------------------------------------------------------------------
# Env / helpers
# -----------------------------------------------------------------------------

def _is_local_env() -> bool:
    try:
        return (settings.app.environment or "local").lower() in {"local", "dev", "development"}
    except Exception:
        return False

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _truncate(s: Any, n: int = 4000) -> str:
    txt = "" if s is None else str(s)
    return txt if len(txt) <= n else (txt[:n] + "…")

def _exc_details() -> Dict[str, Any]:
    et, ev, _tb = sys.exc_info()
    return {"error_type": et.__name__ if et else "Unknown", "error_message": str(ev) if ev else ""}

def _mask(s: Optional[str], prefix: int = 4, suffix: int = 4) -> Optional[str]:
    if not s:
        return None
    if len(s) <= prefix + suffix:
        return s[0] + "*" * max(0, len(s) - 2) + s[-1]
    return f"{s[:prefix]}...{s[-suffix:]}"

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

# -----------------------------------------------------------------------------
# Errors
# -----------------------------------------------------------------------------

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
    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        return
    def log_failure_event(self, kwargs, original_exception, start_time, end_time):
        return

# -----------------------------------------------------------------------------
# Provider & API key resolution
# -----------------------------------------------------------------------------

def _provider(model: Optional[str]) -> str:
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
# JSON normalization (strip fenced blocks, stop double-encoding)
# -----------------------------------------------------------------------------

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)

def coerce_json(content: Any) -> Any:
    """
    Normalize possible JSON outputs from LLMs:
    - dict/list: return as-is
    - non-string: return as-is
    - string:
        * strip ```json fences
        * json.loads once
        * on parse failure, wrap raw string: {"text": <content>}
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
# Message conversion & guarding
# -----------------------------------------------------------------------------

OpenAIMessage = Dict[str, Any]
MessageIn = Union[BaseMessage, Dict[str, Any]]

def _to_openai_message(m: MessageIn) -> Optional[OpenAIMessage]:
    """
    Convert one LangChain BaseMessage OR raw dict into OpenAI-style {role, content}.
    Returns None if content is empty/blank after coercion.
    """
    # Raw dict path
    if isinstance(m, dict):
        role = str(m.get("role") or "").strip() or "user"
        content = m.get("content", "")
        if isinstance(content, (dict, list)):
            msg: OpenAIMessage = {"role": role, "content": content}
        else:
            txt = "" if content is None else str(content)
            if not txt.strip():
                return None
            msg = {"role": role, "content": txt}
        return msg

    # LangChain path
    role = getattr(m, "role", None)
    if not role:
        t = getattr(m, "type", "")
        role = {"human": "user", "ai": "assistant", "system": "system"}.get(t, "user")
    content = getattr(m, "content", "")
    if isinstance(content, (dict, list)):
        return {"role": role, "content": content}
    txt = "" if content is None else str(content)
    if not txt.strip():
        return None
    return {"role": role, "content": txt}

def _lc_to_openai_messages(messages: List[MessageIn]) -> List[OpenAIMessage]:
    """
    Convert a heterogenous list of LangChain messages OR raw dicts into a clean,
    non-empty list of OpenAI-style messages. Blank items are dropped.
    """
    out: List[OpenAIMessage] = []
    for m in messages or []:
        try:
            om = _to_openai_message(m)
            if om:
                out.append(om)
        except Exception:
            continue
    return out

def _ensure_nonempty_messages(messages: List[OpenAIMessage]) -> List[OpenAIMessage]:
    """
    If the prompt would be empty (or all blank), inject a minimal system message.
    """
    if any((m.get("content") or "") for m in messages):
        return messages
    return [{"role": "system", "content": "You are a helpful assistant."}]

# -----------------------------------------------------------------------------
# Helpers (response model checks & schema building)
# -----------------------------------------------------------------------------

def _is_pydantic_cls(x: Any) -> bool:
    try:
        return isinstance(x, type) and issubclass(x, BaseModel)
    except Exception:
        return False

def _is_type_adapter(x: Any) -> bool:
    return hasattr(x, "validate_python") and hasattr(x, "json_schema")

def _to_json_string(obj: Any) -> str:
    try:
        if isinstance(obj, BaseModel):
            return obj.model_dump_json()
    except Exception:
        pass
    try:
        # Handle lists of BaseModels etc.
        return json.dumps(
            obj,
            ensure_ascii=False,
            default=lambda o: (o.model_dump() if hasattr(o, "model_dump") else str(o)),
        )
    except Exception:
        return str(obj)
    
def _force_required_on_object_schema(schema: Dict[str, Any]) -> None:
    """
    OpenAI strict JSON Schema requires every key in `properties` to appear in `required`.
    Recursively normalize all object nodes; also dive into arrays and $defs/definitions.
    Mutates `schema` in place.
    """
    if not isinstance(schema, dict):
        return

    # Normalize object nodes
    if schema.get("type") == "object":
        props = schema.get("properties") or {}
        if isinstance(props, dict):
            schema["required"] = list(props.keys())
            # Recurse into each property schema
            for v in props.values():
                _force_required_on_object_schema(v)

        # Recurse into patternProperties/additionalProperties if present and are schemas
        for k in ("patternProperties", "additionalProperties"):
            v = schema.get(k)
            if isinstance(v, dict):
                if k == "patternProperties":
                    for pp in v.values():
                        _force_required_on_object_schema(pp)
                else:
                    _force_required_on_object_schema(v)

    # Normalize array item schemas
    if schema.get("type") == "array" and "items" in schema:
        _force_required_on_object_schema(schema["items"])

    # Recurse into composition keywords (anyOf/oneOf/allOf)
    for key in ("anyOf", "oneOf", "allOf"):
        if isinstance(schema.get(key), list):
            for sub in schema[key]:
                _force_required_on_object_schema(sub)

    # Dive into modern and legacy defs
    for defs_key in ("$defs", "definitions"):
        defs = schema.get(defs_key)
        if isinstance(defs, dict):
            for sub in defs.values():
                _force_required_on_object_schema(sub)

def _wrap_array_schema(schema: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
    """
    OpenAI structured outputs require a root object. If our schema root is an array,
    wrap it under {"items": <array_schema>} and HOIST any $defs/definitions from the
    inner array schema to the wrapper root so $ref: '#/$defs/...'
    remains valid at the new root.
    """
    if isinstance(schema, dict) and schema.get("type") == "array":
        # Work on a shallow copy to avoid mutating the caller's dict
        inner = dict(schema)

        # Hoist defs so '#/$defs/...' refs resolve at wrapper root
        defs = None
        if "$defs" in inner:
            defs = inner.pop("$defs")
        elif "definitions" in inner:  # older/different emitters
            defs = inner.pop("definitions")

        wrapped = {
            "type": "object",
            "additionalProperties": False,
            "required": ["items"],
            "properties": {"items": inner},
        }
        if defs:
            # Prefer $defs; OpenAI expects modern '$defs'
            wrapped["$defs"] = defs
        return wrapped, True

    return schema, False

def _openai_json_schema_from_adapter(adapter: TypeAdapter, name: str = "Response") -> Tuple[Dict[str, Any], bool]:
    try:
        schema = adapter.json_schema()
        schema, unwrap_items = _wrap_array_schema(schema)

        # 🔧 NEW: force `required` on every object node for strict mode
        _force_required_on_object_schema(schema)

        rf = {
            "type": "json_schema",
            "json_schema": {
                "name": name if not unwrap_items else f"{name}ItemsWrapper",
                "strict": True,
                "schema": schema,
            },
        }
        return rf, unwrap_items
    except Exception:
        return {"type": "json_object"}, False

def _make_adapter_and_response_format(response_model: Any) -> Tuple[TypeAdapter, Any, bool]:
    """
    Accept:
      - BaseModel subclass
      - pydantic TypeAdapter instance
      - typing annotations (e.g., List[Model], Dict[str, Any])
      - bare containers (list, dict, str, int, etc.)
    Returns a (TypeAdapter, response_format_for_provider, unwrap_items_flag)
    """
    # If caller handed us a TypeAdapter, use it and pass a JSON schema to provider.
    if _is_type_adapter(response_model):
        adapter: TypeAdapter = response_model  # type: ignore[assignment]
        rf, unwrap = _openai_json_schema_from_adapter(adapter, name="Root")
        return adapter, rf, unwrap

    # Pydantic model class: adapter + class or schema hint. (Root is object -> no unwrap.)
    if _is_pydantic_cls(response_model):
        adapter = TypeAdapter(response_model)
        # Prefer passing the class when supported; it's already an object root.
        return adapter, response_model, False

    # typing annotations / bare containers: build an adapter and json_schema for provider
    try:
        adapter = TypeAdapter(response_model)
    except Exception:
        # Fallback to a permissive object if the given type is odd
        adapter = TypeAdapter(dict)

    rf, unwrap = _openai_json_schema_from_adapter(adapter, name="Root")
    return adapter, rf, unwrap

# -----------------------------------------------------------------------------
# LLM Service
# -----------------------------------------------------------------------------

class LLMService:
    """
    Unified LLM interface:
      - Provider/model from settings.llm_tools[tool_name]
      - API key resolved via environment variables
      - Retries and structured parsing via LiteLLM
    """

    def __init__(self):
        _silence_external_loggers()
        # Disable background worker / extra logs in LiteLLM
        os.environ["LITELLM_LOG"] = "WARNING"
        os.environ["LITELLM_DEBUG"] = "0"
        os.environ["LITELLM_DISABLE_BACKGROUND_WORKER"] = "1"

        cb = StructlogCallback()
        litellm.success_callback = [cb.log_success_event]
        litellm.failure_callback = [cb.log_failure_event]

    # ------------------- request preparation -------------------

    def _tool_cfg(self, tool_name: str) -> ModelConfig:
        cfg = settings.llm_tools.get(tool_name)
        if cfg is None:
            # Fallback to any configured tool (stable default path)
            return settings.llm_tools.get("question_generator") or next(iter(settings.llm_tools.values()))
        return cfg

    def _prepare_request(
        self,
        tool_name: str,
        messages: List[MessageIn],
        trace_id: Optional[str],
        session_id: Optional[str],
    ) -> Dict[str, Any]:
        cfg = self._tool_cfg(tool_name)
        model_name = cfg.model
        provider_name = _provider(model_name)
        api_key = _env_api_key_for(provider_name)

        oai_messages = _ensure_nonempty_messages(_lc_to_openai_messages(messages))

        kwargs: Dict[str, Any] = {
            "model": model_name,
            "messages": oai_messages,
            "metadata": {"tool_name": tool_name, "trace_id": trace_id, "session_id": session_id},
            "temperature": cfg.temperature,
            "max_tokens": cfg.max_output_tokens,
            "timeout": cfg.timeout_s,
        }
        if api_key:
            kwargs["api_key"] = api_key
        else:
            logger.error("llm.no_api_key", provider=provider_name, tool_name=tool_name)

        # Prompt debug log (compact)
        logger.info(
            "llm_prompt",
            sent_at=_now_iso(),
            tool_name=tool_name,
            model=model_name,
            messages=_compact_messages_for_log(oai_messages),
        )
        return kwargs

    # ------------------- core invoke with retries -------------------

    @retry(
        wait=wait_exponential(multiplier=1, min=1, max=30),
        stop=stop_after_attempt(max(1, settings.agent.max_retries)),
        retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
        reraise=True,
    )
    async def _invoke(self, litellm_kwargs: Dict[str, Any]) -> Any:
        try:
            resp = await litellm.acompletion(**litellm_kwargs)
            return resp
        except ValidationError as e:
            logger.error("llm.validation", error=str(e), exc_info=True)
            raise StructuredOutputError(f"LLM output validation failed: {e}")
        except litellm.exceptions.ContentPolicyViolationError as e:
            logger.error("llm.content_policy_violation", error=str(e))
            raise ContentFilteringError("Request was blocked by content filters.")
        except Exception as e:
            details = _exc_details()
            logger.error("llm.unexpected_error", error=str(e), **details, exc_info=True)
            raise LLMAPIError(f"Unexpected LLM API error: {e}")

    # ------------------- public entrypoints -------------------

    async def get_agent_response(
        self,
        tool_name: str,
        messages: List[MessageIn],
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
                logger.error("llm.tool_calls_parse_fail", error=str(e), exc_info=True)

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
        messages: List[MessageIn],
        response_model: Any,
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
        as_dict: bool = False,  # kept for compat; ignored
    ) -> Any:
        """
        Request response validated into the requested shape.

        Supports:
          - Pydantic BaseModel subclasses (preferred when available)
          - pydantic TypeAdapter(List[Model]) / TypeAdapter[...]
          - typing annotations (List[Model], Dict[str, Any], etc.)
          - bare containers (list, dict, str, int, float, bool)

        OpenAI requires response_format schemas with root type=object. If the
        requested schema is an array at the root (e.g., List[Model]), we wrap it
        into {"items": <array_schema>} and unwrap it after parsing.
        """
        # Build adapter & best-effort provider response_format
        adapter, response_format, unwrap_items = _make_adapter_and_response_format(response_model)

        req = self._prepare_request(tool_name, messages, trace_id, session_id)
        # Provide structured output hints to provider when possible.
        if response_format is not None:
            # 🔧 Ensure strict schemas are compliant even if built elsewhere
            if isinstance(response_format, dict) and response_format.get("type") == "json_schema":
                js = response_format.get("json_schema") or {}
                if js.get("strict") and "schema" in js:
                    _force_required_on_object_schema(js["schema"])
            req["response_format"] = response_format


        response = await self._invoke(req)

        # Some providers bubble a refusal object
        refusal = getattr(response, "refusal", None)
        if not refusal:
            msg0 = getattr(response, "choices", [None])[0]
            msg0 = getattr(msg0, "message", None)
            refusal = getattr(msg0, "refusal", None) if msg0 else None
        if refusal:
            txt = getattr(refusal, "message", None) or str(refusal)
            logger.error("llm_structured_refusal", tool_name=tool_name, refusal=str(txt))
            raise StructuredOutputError(f"Provider refused structured output: {txt}")

        # Preferred: provider-parsed structured output
        parsed = getattr(response.choices[0].message, "parsed", None)
        if parsed is not None:
            try:
                if isinstance(parsed, BaseModel):
                    data = parsed.model_dump()
                else:
                    data = parsed
                if unwrap_items and isinstance(data, dict) and "items" in data:
                    data = data["items"]
                out = adapter.validate_python(data)
                logger.info(
                    "llm_response",
                    received_at=_now_iso(),
                    tool_name=tool_name,
                    model=req.get("model"),
                    json=_truncate(_to_json_string(out), 4000),
                )
                return out
            except Exception as e:
                logger.debug("llm.parsed_validation_fallback", error=str(e), exc_info=True)
                # Fall through to content parsing

        # Fallback: parse from content (strip fences, load JSON, validate)
        content = response.choices[0].message.content
        try:
            data = coerce_json(content)
            if unwrap_items and isinstance(data, dict) and "items" in data:
                data = data["items"]
            out = adapter.validate_python(data)
            logger.info(
                "llm_response",
                received_at=_now_iso(),
                tool_name=tool_name,
                model=req.get("model"),
                json=_truncate(_to_json_string(out), 4000),
            )
            return out
        except Exception as e:
            logger.error("llm.structured_parse_failed", tool_name=tool_name, error=str(e), exc_info=True)
            raise StructuredOutputError(f"LLM did not return structured output: {e}")

    async def get_text_response(
        self,
        tool_name: str,
        messages: List[MessageIn],
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> str:
        req = self._prepare_request(tool_name, messages, trace_id, session_id)

        response = await self._invoke(req)
        content = response.choices[0].message.content
        text = content if isinstance(content, str) else (content or "")

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
            logger.error("embeddings.unavailable", reason=_embed_import_error or "unknown")
            return []
        try:
            loop = asyncio.get_running_loop()

            def _encode(texts: List[str]) -> List[List[float]]:
                vecs = _embed_model.encode(texts, normalize_embeddings=True)  # type: ignore[attr-defined]
                return vecs.tolist() if hasattr(vecs, "tolist") else [list(vecs)]

            out = await loop.run_in_executor(None, _encode, input)
            return out
        except Exception as e:
            logger.error("embeddings.failed", error=str(e), exc_info=True)
            return []

# Singleton
def get_llm_service() -> LLMService:
    return LLMService()

llm_service = get_llm_service()
