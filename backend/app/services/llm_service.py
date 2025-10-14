# backend/app/services/llm_service.py
from __future__ import annotations

"""
LLM Service — OpenAI Responses API via LiteLLM (hardened)

This rewrite addresses a parsing bug where providers sometimes return a list of SDK
objects (e.g., ResponseOutputText(...)) inside `resp.output[*].content`.
Previously we could pass that list straight into Pydantic, causing errors like:

  Input should be a valid dictionary or instance of <Model> [input_type=list]

Fix:
- When encountering lists in Responses output, only accept them if they are JSON-like
  (dict/list/primitives). If the list appears to be SDK objects, extract their text,
  join, and attempt to parse JSON from that text before returning.
- Additional robustness in text extraction to read `.text` / `.content` from SDK objects.
- Same API surface; improvements are internal and PII-safe logged.

Other characteristics:
- End-to-end try/except coverage with structured logs.
- Consistent payload building (Responses API & response_format envelopes).
- Local validation with Pydantic/TypeAdapter when available.
- Safer streaming handling with error surfacing.
"""

import asyncio
import json
import re
import time
import typing as t
from typing import Any, Dict, List, Optional, Union

import litellm
import structlog
from pydantic import ValidationError
from pydantic.type_adapter import TypeAdapter

from app.core.config import settings

logger = structlog.get_logger(__name__)

# -----------------------
# Global LiteLLM toggles
# -----------------------
try:
    litellm.set_verbose = bool(getattr(settings, "LITELLM_VERBOSE", False))
except Exception:
    pass

try:
    litellm.enable_json_schema_validation = bool(
        getattr(settings, "ENABLE_JSON_SCHEMA_VALIDATION", True)
    )
except Exception:
    pass


# -----------------------
# Small general utilities
# -----------------------
RoleMsg = Dict[str, Any]  # {"role": "system"|"user"|"assistant", "content": str|list[...]}

_JSON_PRIMS = (str, int, float, bool, type(None))


def _safe_len(x) -> Optional[int]:
    try:
        return len(x)  # type: ignore[arg-type]
    except Exception:
        return None


def _truncate(s: str, n: int = 500) -> str:
    if not isinstance(s, str):
        try:
            s = str(s)
        except Exception:
            return ""
    return s if len(s) <= n else (s[: n - 3] + "...")


def _timing_ms(t0: float) -> float:
    return round((time.perf_counter() - t0) * 1000.0, 1)


def coerce_json(obj: Any) -> Any:
    """
    Best-effort coercion to JSON-like python types.
    - Pydantic model -> dict
    - str -> json.loads if possible, else {"value": str}
    - dict/list -> as-is
    - other -> as-is
    """
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            pass
    if isinstance(obj, (dict, list)):
        return obj
    if isinstance(obj, str):
        try:
            return json.loads(obj)
        except Exception:
            return {"value": obj}
    return obj


def _lc_to_responses_input(messages: Any) -> List[RoleMsg]:
    """
    Convert LangChain/BaseMessage-like objects or plain dicts to
    Responses API "input" message list.
    """
    out: List[RoleMsg] = []
    try:
        for m in messages or []:
            # Already dict-shaped (Responses style)
            if isinstance(m, dict) and "role" in m and "content" in m:
                out.append({"role": m["role"], "content": m["content"]})
                continue

            # LangChain BaseMessage-like
            role = getattr(m, "type", None) or getattr(m, "role", None) or "user"
            if role == "human":
                role = "user"
            if role == "ai":
                role = "assistant"

            content = getattr(m, "content", None)
            if content is None and isinstance(m, dict):
                content = m.get("content")

            out.append({"role": role, "content": content})
    except Exception as e:
        logger.error("llm.input.normalize.fail", error=str(e), sample=_truncate(str(messages), 300))
        # Try best-effort fallback
        out = [{"role": "user", "content": _truncate(str(messages), 5000)}]
    return out


def _extract_json_str(txt: str) -> str:
    """
    Extract the first JSON object/array from a text blob for lenient parsing.
    """
    m = re.search(r"\{.*\}|\[.*\]", txt, re.DOTALL)
    return m.group(0) if m else txt


def _try_parse_json_from_text(s: str) -> Optional[Any]:
    if not isinstance(s, str):
        return None
    st = s.lstrip()
    if not st.startswith("{") and not st.startswith("["):
        return None
    try:
        return json.loads(_extract_json_str(s))
    except Exception:
        return None


def _is_json_like_list(val: Any) -> bool:
    """
    True if val is a list composed only of JSON-like items (dict/list/primitives/None).
    """
    if not isinstance(val, list):
        return False
    for el in val:
        if isinstance(el, (dict, list)):
            continue
        if isinstance(el, _JSON_PRIMS):
            continue
        return False
    return True


def _extract_text_fields_from_obj(obj: Any) -> List[str]:
    """
    Provider-agnostic attempt to read text-like fields from an SDK object or dict.
    """
    texts: List[str] = []
    try:
        # Common attributes
        for attr in ("text", "content", "output_text"):
            v = getattr(obj, attr, None)
            if isinstance(v, str) and v.strip():
                texts.append(v)
        if isinstance(obj, dict):
            for key in ("text", "content", "output_text"):
                v = obj.get(key)
                if isinstance(v, str) and v.strip():
                    texts.append(v)
    except Exception:
        pass
    return texts


def _coerce_list_from_sdk_to_json(val: List[Any]) -> Optional[Any]:
    """
    If `val` looks like a list of SDK objects (not JSON-like), try to join their text fields
    and parse JSON from that joined text. If that fails, return None.
    """
    if _is_json_like_list(val):
        return val

    # Attempt to read `.text` / `.content` from elements
    texts: List[str] = []
    for el in val:
        if isinstance(el, (dict, list, *_JSON_PRIMS)):
            # Skip JSON-like; if we had any non-JSON-like elements, we try a text join below.
            continue
        texts.extend(_extract_text_fields_from_obj(el))

    # Also scan dict elements for text fields if the list is mixed
    if not texts:
        for el in val:
            if isinstance(el, dict):
                texts.extend(_extract_text_fields_from_obj(el))

    joined = "\n".join([t for t in texts if isinstance(t, str) and t.strip()])
    parsed = _try_parse_json_from_text(joined) if joined else None
    if parsed is not None:
        logger.debug("llm.extract.coerced_sdk_list_to_json", size=len(val), text_len=len(joined))
        return parsed

    # No usable JSON; we refuse to return the raw provider list to Pydantic.
    logger.debug("llm.extract.sdk_list_refused", size=len(val))
    return None


def _extract_text_from_response(resp: Any) -> str:
    """
    Extract best-effort text from a non-streaming Responses object.
    LiteLLM often exposes `.output_text`, but we cover multiple shapes and SDK lists.
    """
    # Direct, simple properties
    try:
        for key in ("output_text", "content", "text"):
            if hasattr(resp, key):
                val = getattr(resp, key)
                if isinstance(val, str) and val.strip():
                    return val
    except Exception:
        pass

    parts: List[str] = []
    try:
        output = getattr(resp, "output", None) or []
        for item in output:
            # Most providers attach text on the item directly
            direct = getattr(item, "text", None) or getattr(item, "content", None)
            if isinstance(direct, str):
                parts.append(direct)
                continue

            # Some providers attach a list of blocks (SDK objects OR dicts)
            if isinstance(direct, list):
                # Read blocks: prefer dict blocks, but also handle SDK objects
                for block in direct:
                    if isinstance(block, dict):
                        if isinstance(block.get("text"), str):
                            parts.append(block["text"])
                        elif isinstance(block.get("content"), str):
                            parts.append(block["content"])
                    else:
                        parts.extend(_extract_text_fields_from_obj(block))
            # Dict-shaped item
            if isinstance(item, dict):
                t1 = item.get("text") or item.get("content")
                if isinstance(t1, str):
                    parts.append(t1)
                elif isinstance(t1, list):
                    for blk in t1:
                        if isinstance(blk, dict):
                            if isinstance(blk.get("text"), str):
                                parts.append(blk["text"])
                            elif isinstance(blk.get("content"), str):
                                parts.append(blk["content"])
                        else:
                            parts.extend(_extract_text_fields_from_obj(blk))
    except Exception as e:
        logger.debug("llm.extract_text.warn", error=str(e))

    return "\n".join([s for s in parts if isinstance(s, str)]).strip()


async def _run_in_thread(func, *args, **kwargs):
    return await asyncio.to_thread(func, *args, **kwargs)


def _is_reasoning_family(model: str) -> bool:
    """Treat GPT-5 and O-series (o3/o4) as 'reasoning' families."""
    m = (model or "").lower()
    return m.startswith(("gpt-5", "o3", "o4"))


def _supports_text_params(model: str) -> bool:
    """Only non-reasoning models should receive `text` (temperature/top_p/etc.)."""
    return not _is_reasoning_family(model)


def _merge(a: Optional[Dict[str, Any]], b: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    base = dict(a or {})
    for k, v in (b or {}).items():
        base[k] = v
    return base


def _default_metadata(
    tool: Optional[str],
    trace_id: Optional[str],
    session_id: Optional[str],
    extra: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    meta: Dict[str, Any] = {}
    if tool:
        meta["tool"] = tool
    if trace_id:
        meta["trace_id"] = trace_id
    if session_id:
        meta["session_id"] = session_id
    if extra:
        meta.update({k: v for k, v in extra.items() if v is not None})
    return meta


def _sanitize_schema_name(name: str) -> str:
    """Some providers expect a short, simple name."""
    s = re.sub(r"[^A-Za-z0-9_\-]", "_", str(name or "response"))
    return s[:64] or "response"


def _schema_envelope(name: str, schema: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": _sanitize_schema_name(name),
            "schema": schema,
            "strict": True,
        },
    }


# =================
# LLM Service class
# =================
class LLMService:
    def __init__(self):
        # Fallback defaults; prefer setting these via settings.*
        self.default_model = getattr(settings, "LLM_MODEL_DEFAULT", "gpt-5-mini")
        self.timeout_s = int(getattr(settings, "LLM_REQUEST_TIMEOUT_S", 60))
        self.max_tokens = int(getattr(settings, "LLM_MAX_OUTPUT_TOKENS", 2048))

        # Optional per-family defaults
        self._text_defaults = getattr(settings, "LLM_TEXT_DEFAULT", None) or {}
        self._reasoning_defaults = getattr(settings, "LLM_REASONING_DEFAULT", None) or {}
        self._truncation_default = getattr(settings, "LLM_TRUNCATION", None)

        # Embeddings default
        self.embedding_model = getattr(
            settings, "LLM_EMBEDDING_MODEL_DEFAULT", "text-embedding-3-small"
        )

    # ----------------
    # Payload building
    # ----------------
    def _build_payload(
        self,
        *,
        model: Optional[str],
        messages: Any,
        max_output_tokens: Optional[int],
        text_params: Optional[Dict[str, Any]],
        reasoning: Optional[Dict[str, Any]],
        truncation: Optional[str],
        metadata: Optional[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        tool_choice: Optional[Union[str, Dict[str, Any]]],
        stream: bool,
    ) -> Dict[str, Any]:
        try:
            mdl = model or self.default_model
            payload: Dict[str, Any] = {
                "model": mdl,
                "input": _lc_to_responses_input(messages),
                "timeout": self.timeout_s,
                "max_output_tokens": int(max_output_tokens or self.max_tokens),
            }

            # Family routing
            if _supports_text_params(mdl):
                merged_text = _merge(self._text_defaults, text_params)
                if merged_text:
                    payload["text"] = merged_text  # e.g., {"temperature": 0.3, "top_p": 1.0}
            else:
                merged_reasoning = _merge(self._reasoning_defaults, reasoning)
                if merged_reasoning:
                    payload["reasoning"] = merged_reasoning  # e.g., {"effort": "low"}

            # Misc
            if truncation or self._truncation_default:
                payload["truncation"] = truncation or self._truncation_default
            if metadata:
                payload["metadata"] = metadata
            if tools:
                payload["tools"] = tools
            if tool_choice:
                payload["tool_choice"] = tool_choice
            if stream:
                payload["stream"] = True

            logger.debug(
                "llm.payload.built",
                model=mdl,
                has_tools=bool(tools),
                tool_choice=tool_choice if isinstance(tool_choice, str) else ("object" if tool_choice else None),
                stream=stream,
                max_output_tokens=payload.get("max_output_tokens"),
                input_msgs=_safe_len(payload["input"]),
            )
            return payload
        except Exception as e:
            logger.error("llm.payload.build.fail", error=str(e))
            # Minimal fallback to avoid crashing upstream; this will likely fail fast but logs the root cause.
            return {
                "model": model or self.default_model,
                "input": [{"role": "user", "content": "Internal error: could not build payload."}],
                "timeout": self.timeout_s,
                "max_output_tokens": int(max_output_tokens or self.max_tokens),
            }

    # -----------------------------------------
    # Core Responses invocation + robust parsing
    # -----------------------------------------
    def _extract_structured_from_responses(self, resp: Any) -> Any:
        """
        Robust extraction for Responses API structured outputs.

        Priority:
          1) resp.output_parsed (provider-populated)
          2) JSON-like dict/list under resp.output[*].{parsed,json,data,content}
          3) dict/list inside content blocks (when `content` is a list)
          4) Parse JSON-looking text from output_text as a last resort
          5) For lists of SDK objects, join their text and parse as JSON
        """
        # 1) Direct parsed object
        try:
            parsed = getattr(resp, "output_parsed", None)
            if isinstance(parsed, (dict, list)):
                return parsed
        except Exception:
            pass

        # 2) Walk `output` structure (attribute or dict forms)
        try:
            output = getattr(resp, "output", None) or []
            for item in output:
                # Attribute-style access
                for key in ("parsed", "json", "data", "content"):
                    val = getattr(item, key, None)
                    if val is None and isinstance(item, dict):
                        val = item.get(key)

                    # Accept plain dicts immediately
                    if isinstance(val, dict):
                        return val

                    # Handle lists carefully (could be JSON-like or SDK objects)
                    if isinstance(val, list):
                        # JSON-like? return as-is
                        if _is_json_like_list(val):
                            return val

                        # Try to coerce SDK-list to JSON
                        coerced = _coerce_list_from_sdk_to_json(val)
                        if coerced is not None:
                            return coerced

                        # If content is list of dict blocks, scan them too
                        for block in val:
                            if not isinstance(block, dict):
                                continue
                            # direct JSON in block
                            for bk in ("parsed", "json", "data", "content"):
                                bv = block.get(bk)
                                if isinstance(bv, dict) or _is_json_like_list(bv):
                                    return bv
                            # text fallback
                            bt = block.get("text") or block.get("content")
                            parsed_bt = _try_parse_json_from_text(bt) if isinstance(bt, str) else None
                            if parsed_bt is not None:
                                return parsed_bt
        except Exception as e:
            logger.debug("llm.extract_structured.warn", error=str(e))

        # 3) Fallback: parse from text properties
        try:
            txt = _extract_text_from_response(resp)
            parsed_txt = _try_parse_json_from_text(txt)
            if parsed_txt is not None:
                return parsed_txt
        except Exception as e:
            logger.debug("llm.extract_structured.text_parse.warn", error=str(e))

        return None

    async def _responses_create_and_parse(self, req: Dict[str, Any]) -> Any:
        """
        Execute `litellm.responses(**req)` in a worker thread and
        return a parsed structured object (dict/list) if possible.
        """
        t0 = time.perf_counter()
        try:
            resp = await _run_in_thread(litellm.responses, **req)
        except Exception as e:
            logger.error(
                "llm.responses.call.fail",
                error=str(e),
                model=req.get("model"),
                tool=(req.get("metadata") or {}).get("tool"),
                trace_id=(req.get("metadata") or {}).get("trace_id"),
                session_id=(req.get("metadata") or {}).get("session_id"),
            )
            raise

        try:
            logger.debug(
                "llm.responses.raw",
                response_id=getattr(resp, "id", None),
                has_output_parsed=bool(getattr(resp, "output_parsed", None)),
                has_output=bool(getattr(resp, "output", None)),
                latency_ms=_timing_ms(t0),
            )
        except Exception:
            pass

        structured = None
        try:
            structured = self._extract_structured_from_responses(resp)
        except Exception as e:
            logger.debug("llm.responses.parse.warn", error=str(e))

        if structured is not None:
            return structured

        # As a very last resort, return whatever text we have (useful for debugging)
        try:
            txt = _extract_text_from_response(resp)
            if txt:
                parsed = _try_parse_json_from_text(txt)
                if parsed is not None:
                    return parsed
                logger.error("llm.responses.parse_err.text_only", sample=_truncate(txt, 800))
        except Exception as e:
            logger.error("llm.responses.parse_err.extract_text_fail", error=str(e))

        logger.error("llm.responses.parse_err", err="No structured output and no parseable text")
        raise RuntimeError("No structured output and no parseable text from Responses API")

    # -----------------
    # Text generation
    # -----------------
    async def get_text(
        self,
        messages: Any,
        *,
        model: Optional[str] = None,
        max_output_tokens: Optional[int] = None,
        stream: bool = False,
        text_params: Optional[Dict[str, Any]] = None,
        reasoning: Optional[Dict[str, Any]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Optional[Union[str, Dict[str, Any]]] = None,
        truncation: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        # Observability
        tool: Optional[str] = None,
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
        # Provider-specific web search options
        web_search_options: Optional[dict] = None,
    ) -> Union[str, t.AsyncGenerator[str, None]]:
        """
        Basic text generation wrapper (optionally streaming).
        - Non-streaming: returns final output_text as string.
        - Streaming: async generator yielding only output_text deltas.
        """
        # Map web_search tool type based on provider (openai vs preview flag)
        req_tools = None
        if tools:
            try:
                prov = getattr(getattr(settings, "llm", object()), "provider", "openai")
            except Exception:
                prov = "openai"
            mapped = []
            for tdef in tools:
                try:
                    tcopy = dict(tdef) if isinstance(tdef, dict) else tdef
                    if isinstance(tcopy, dict) and tcopy.get("type") in {"web_search", "web_search_preview"}:
                        tcopy = dict(tcopy)
                        tcopy["type"] = "web_search" if str(prov).lower() == "openai" else "web_search_preview"
                        if web_search_options and str(prov).lower() != "openai":
                            tcopy["web_search_options"] = web_search_options
                    mapped.append(tcopy)
                except Exception as e:
                    logger.debug("llm.tools.map.warn", error=str(e), tool_def=str(tdef))
            req_tools = mapped

        meta = _default_metadata(tool, trace_id, session_id, metadata)
        payload = self._build_payload(
            model=model,
            messages=messages,
            max_output_tokens=max_output_tokens,
            text_params=text_params,
            reasoning=reasoning,
            truncation=truncation,
            metadata=meta,
            tools=req_tools,
            tool_choice=tool_choice,
            stream=stream,
        )

        if stream:
            # LiteLLM returns an iterator of Responses API events
            def _start_stream():
                return litellm.responses(**payload)

            async def _aiter():
                t0 = time.perf_counter()
                try:
                    stream_iter = await _run_in_thread(_start_stream)
                except Exception as e:
                    logger.error(
                        "llm.stream.start.fail",
                        error=str(e),
                        model=payload.get("model"),
                        tool=tool,
                        trace_id=trace_id,
                        session_id=session_id,
                    )
                    return

                try:
                    for event in stream_iter:
                        etype = getattr(event, "type", "") or ""
                        if etype in (
                            "response.output_text.delta",
                            "response.delta",
                            "response.message.delta",
                        ):
                            # Try all common delta fields
                            delta = getattr(event, "delta", None)
                            if isinstance(delta, str) and delta:
                                yield delta
                                continue
                            text = getattr(event, "text", None)
                            if isinstance(text, str) and text:
                                yield text
                                continue
                        elif etype in ("response.completed", "response.message.completed"):
                            logger.info(
                                "llm.stream.completed",
                                latency_ms=_timing_ms(t0),
                                model=payload.get("model"),
                                tool=tool,
                                trace_id=trace_id,
                                session_id=session_id,
                            )
                            break
                        elif etype in ("response.error",):
                            # Surface stream errors in logs and stop
                            logger.error(
                                "llm.stream.error",
                                event=str(event),
                                model=payload.get("model"),
                                tool=tool,
                                trace_id=trace_id,
                                session_id=session_id,
                            )
                            break
                except Exception as e:
                    logger.error("llm.stream.iter.fail", error=str(e))
                return

            return _aiter()

        # Non-streaming call
        t0 = time.perf_counter()
        try:
            resp = await _run_in_thread(litellm.responses, **payload)
        except Exception as e:
            logger.error(
                "llm.text.call.fail",
                error=str(e),
                model=payload.get("model"),
                tool=tool,
                trace_id=trace_id,
                session_id=session_id,
            )
            raise

        try:
            text = _extract_text_from_response(resp)
        except Exception as e:
            logger.error("llm.text.extract.fail", error=str(e))
            text = ""

        logger.info(
            "llm.text.ok",
            model=payload.get("model"),
            response_id=getattr(resp, "id", None),
            tool=tool,
            trace_id=trace_id,
            session_id=session_id,
            latency_ms=_timing_ms(t0),
            chars=len(text or ""),
        )
        return text

    # ------------------------
    # Structured output (JSON)
    # ------------------------
    async def get_structured_response(
        self,
        *,
        tool_name: str,
        messages: Any,
        response_model: Any,
        response_format: Optional[dict] = None,  # explicit wire schema OK
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ):
        """
        Wire schema & validation rules:
          • If `response_format` is a JSON Schema envelope, pass it through.
          • If `response_format` is {"name","schema"}, wrap it to Responses API envelope.
          • Else if `response_model` is Pydantic/TypeAdapter, derive JSON Schema and wrap it.
          • Else pass response_model as-is (last-resort).
        Then, always validate locally when a Pydantic model/TypeAdapter is available.
        """
        rf: Optional[Dict[str, Any]] = None
        validate_with: Any = None  # keep original model/adapter for local validation

        # Prefer an explicitly provided response_format for the wire schema,
        # but still validate with the typed response_model when possible.
        try:
            if response_format is not None:
                if isinstance(response_format, dict) and response_format.get("type") == "json_schema":
                    rf = response_format
                elif isinstance(response_format, dict) and "schema" in response_format:
                    name = response_format.get("name") or tool_name or "response"
                    rf = _schema_envelope(name, response_format["schema"])
                else:
                    rf = response_format  # pass through unknown shapes

                # Attempt to keep a local validator from response_model
                try:
                    _ = response_model if isinstance(response_model, TypeAdapter) else TypeAdapter(response_model)
                    validate_with = response_model
                except Exception:
                    validate_with = None
            else:
                # Back-compat path: derive schema from the model or accept an envelope.
                if isinstance(response_model, dict) and "schema" in response_model:
                    name = response_model.get("name") or tool_name or "response"
                    rf = _schema_envelope(name, response_model["schema"])
                    validate_with = None
                else:
                    try:
                        adapter = response_model if isinstance(response_model, TypeAdapter) else TypeAdapter(response_model)
                        schema = adapter.json_schema()
                        name = schema.get("title") or getattr(response_model, "__name__", tool_name) or tool_name
                        rf = _schema_envelope(name, schema)
                        validate_with = response_model
                    except Exception:
                        rf = response_model
                        validate_with = None
        except Exception as e:
            logger.error("llm.structured.schema_build.fail", error=str(e), tool=tool_name)

        req = {
            "model": getattr(settings, "LLM_MODEL_DEFAULT", self.default_model),
            "input": _lc_to_responses_input(messages),
            "response_format": rf,
            "metadata": _default_metadata(tool_name, trace_id, session_id, None),
            "timeout": int(getattr(settings, "LLM_REQUEST_TIMEOUT_S", self.timeout_s)),
            "max_output_tokens": int(getattr(settings, "LLM_MAX_OUTPUT_TOKENS", self.max_tokens)),
        }

        t0 = time.perf_counter()
        try:
            parsed = await self._responses_create_and_parse(req)
        except Exception as e:
            logger.error(
                "llm.structured.call_or_parse.fail",
                error=str(e),
                model=req.get("model"),
                tool=tool_name,
                trace_id=trace_id,
                session_id=session_id,
            )
            raise

        # Local validation (even when an explicit response_format was provided)
        try:
            if isinstance(validate_with, TypeAdapter):
                out = validate_with.validate_python(parsed)
            elif hasattr(validate_with, "model_validate"):  # pydantic v2 BaseModel class
                out = validate_with.model_validate(parsed)
            else:
                out = parsed
        except ValidationError as ve:
            schema_name = None
            try:
                schema_name = (rf or {}).get("json_schema", {}).get("name")
            except Exception:
                pass
            logger.error(
                "llm.responses.validation_err",
                tool=tool_name,
                schema_name=schema_name,
                err=str(ve),
                trace_id=trace_id,
                session_id=session_id,
            )
            raise

        logger.info(
            "llm.structured.ok",
            tool=tool_name,
            model=req.get("model"),
            latency_ms=_timing_ms(t0),
            trace_id=trace_id,
            session_id=session_id,
            has_validator=bool(validate_with),
            parsed_type=type(parsed).__name__,
        )
        return out

    # -------------
    # Embeddings
    # -------------
    async def get_embedding(
        self,
        input: Union[str, List[str]],
        *,
        model: Optional[str] = None,
        timeout: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> List[List[float]]:
        """
        Thin wrapper around litellm.embedding (Embeddings API).
        Returns: list of vectors (one per input string).
        """
        mdl = model or getattr(settings, "LLM_EMBEDDING_MODEL_DEFAULT", self.embedding_model)
        t0 = time.perf_counter()
        try:
            resp = await _run_in_thread(
                litellm.embedding,
                model=mdl,
                input=input,
                timeout=timeout or int(getattr(settings, "LLM_REQUEST_TIMEOUT_S", self.timeout_s)),
                metadata=_default_metadata("embedding", trace_id, session_id, metadata),
            )
        except Exception as e:
            logger.error("llm.embedding.call.fail", error=str(e), model=mdl, trace_id=trace_id, session_id=session_id)
            raise

        try:
            # Normalized shape: {"data": [{"embedding": [...]}...]}
            data = getattr(resp, "data", None) or (resp.get("data") if isinstance(resp, dict) else None)  # type: ignore[union-attr]
            out: List[List[float]] = []
            for item in data or []:
                emb = getattr(item, "embedding", None) or (item.get("embedding") if isinstance(item, dict) else None)  # type: ignore[union-attr]
                if emb:
                    out.append(list(emb))
            logger.info(
                "llm.embedding.ok",
                vectors=len(out),
                model=mdl,
                latency_ms=_timing_ms(t0),
                trace_id=trace_id,
                session_id=session_id,
            )
            return out
        except Exception as e:
            logger.error("llm.embedding.parse.fail", error=str(e), model=mdl, trace_id=trace_id, session_id=session_id)
            raise

    # -------------
    # Admin helpers
    # -------------
    async def get_by_id(self, response_id: str):
        try:
            return await _run_in_thread(litellm.get_responses, response_id=response_id)
        except Exception as e:
            logger.error("llm.responses.get_by_id.fail", error=str(e), response_id=response_id)
            raise

    async def delete_by_id(self, response_id: str):
        try:
            return await _run_in_thread(litellm.delete_responses, response_id=response_id)
        except Exception as e:
            logger.error("llm.responses.delete_by_id.fail", error=str(e), response_id=response_id)
            raise


# Singleton
llm_service = LLMService()
