from __future__ import annotations

"""
A resilient wrapper over LiteLLM Responses API that guarantees structured
(JSON-schema validated) outputs when requested and robustly extracts JSON
from provider responses even when nested objects (not plain dicts) are
returned inside `output[].content[]`.

Key improvements vs. previous version
-------------------------------------
1) Deep object-to-dict coercion for `output[]` and nested `content[]` parts.
2) More permissive text harvesting that does NOT drop non-dict content parts.
3) Safer, clearer JSON parsing with code-fence stripping and balanced-block scan.
4) Better logging and error messages, including previews of candidates.
5) Same public API: `LLMService.get_structured_response(...)`.

Notes
-----
- Requires: `litellm`, `structlog`, `pydantic>=2` (TypeAdapter available).
- Uses app.settings for defaults (model, timeouts, etc.).
"""

import asyncio
import dataclasses
import json
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

import litellm
import structlog
from pydantic import ValidationError
from pydantic.type_adapter import TypeAdapter

from app.core.config import settings

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------

class StructuredOutputError(RuntimeError):
    """Raised when we cannot extract/validate structured output."""

    def __init__(self, message: str, *, preview: str | None = None) -> None:
        super().__init__(message)
        self.preview = preview


# ---------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*[\r\n]+(.*?)[\r\n]+```\s*$", re.DOTALL | re.IGNORECASE)


def coerce_json(obj: Any) -> Any:
    """Best-effort conversion for logging/metadata serialization."""
    # pydantic v2
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            pass
    # dataclass
    if dataclasses.is_dataclass(obj):
        try:
            return dataclasses.asdict(obj)
        except Exception:
            pass
    # generic object
    if hasattr(obj, "__dict__"):
        try:
            return dict(obj.__dict__)
        except Exception:
            pass
    if isinstance(obj, (dict, list, str, int, float, bool)) or obj is None:
        return obj
    try:
        return str(obj)
    except Exception:
        return "<unserializable>"


def _asdict_shallow(obj: Any) -> Optional[Dict[str, Any]]:
    """Coerce common object-like containers into a dict without deep recursion."""
    if isinstance(obj, dict):
        return obj
    # pydantic v2
    if hasattr(obj, "model_dump"):
        try:
            d = obj.model_dump()
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    # dataclass
    if dataclasses.is_dataclass(obj):
        try:
            d = dataclasses.asdict(obj)
            if isinstance(d, dict):
                return d
        except Exception:
            pass
    # generic object namespace
    if hasattr(obj, "__dict__"):
        try:
            return dict(obj.__dict__)
        except Exception:
            return None
    return None


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _shape_preview(obj: Any, limit: int = 1000) -> str:
    """Preview object shape/keys for logs without dumping huge payloads."""
    try:
        if isinstance(obj, dict):
            return json.dumps({"_keys": list(obj.keys())[:20]}, ensure_ascii=False)[:limit]
        if isinstance(obj, list):
            return json.dumps(obj[:2], ensure_ascii=False)[:limit]
        return str(obj)[:limit]
    except Exception:
        return "<unpreviewable>"


# ------------------------- text -> JSON helpers -------------------------

def _strip_code_fences(s: str) -> str:
    """Remove a single top-level ```json ...``` or ```...``` fence if present."""
    m = _FENCE_RE.match(s)
    return m.group(1).strip() if m else s.strip()


def _find_first_balanced_json(s: str) -> Optional[str]:
    """
    Find the first balanced JSON object/array substring in s.
    Returns the substring or None.
    """
    s = s.strip()
    if not s:
        return None
    # Fast path
    if s[0] in "[{":
        # Try simple validation length guard; full json.loads happens upstream.
        return s

    pairs: List[Tuple[str, str]] = [("{", "}"), ("[", "]")]
    for opener, closer in pairs:
        start = s.find(opener)
        if start == -1:
            continue
        depth = 0
        in_str = False
        esc = False
        for i, ch in enumerate(s[start:], start):
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == opener:
                    depth += 1
                elif ch == closer:
                    depth -= 1
                    if depth == 0:
                        return s[start : i + 1]
    return None


def _parse_json_from_text(s: str) -> Optional[Any]:
    """Parse JSON from a string, trying code-fence stripping then balanced scan."""
    if not isinstance(s, str) or not s.strip():
        return None
    s = _strip_code_fences(s)
    # Direct parse if likely JSON
    if s and s[0] in "[{":
        try:
            return json.loads(s)
        except Exception:
            pass
    chunk = _find_first_balanced_json(s)
    if chunk:
        try:
            return json.loads(chunk)
        except Exception:
            return None
    return None


# ---------------------------- response harvest ----------------------------

DefItem = Dict[str, Any]


def _iter_output_items(resp: Any) -> Iterable[DefItem]:
    """Yield output items as dicts. Accept dicts or object-like with __dict__/model_dump."""
    output = None
    if isinstance(resp, dict):
        output = resp.get("output")
    else:
        output = getattr(resp, "output", None)
        if output is None and hasattr(resp, "__dict__"):
            output = getattr(resp, "__dict__", {}).get("output")
    if isinstance(output, list):
        for item in output:
            if isinstance(item, dict):
                yield item
            else:
                d = _asdict_shallow(item)
                if d is not None:
                    yield d


def _collect_text_parts(resp: Any) -> List[str]:
    """
    Collect candidate text blobs, in order of likelihood:
      - output[].content[].text where the part is output_text/text (skip reasoning)
      - top-level convenience `output_text` if present (liteLLM sometimes exposes it)
      - legacy chat completions fallbacks: choices[0].message.content / choices[0].text
      - other top-level text/value fields when present
    """
    candidates: List[str] = []

    # 1) Responses API path
    for item in _iter_output_items(resp):
        if _get(item, "type") == "reasoning":
            continue
        content = _get(item, "content") or []
        if isinstance(content, list):
            for part in content:
                pd = _asdict_shallow(part) or part
                ptype = _get(pd, "type")
                # Prefer explicit text-bearing parts
                if ptype in (None, "output_text", "text"):
                    t = _get(pd, "text")
                    if isinstance(t, str) and t.strip():
                        candidates.append(t)
                # Fallback fields sometimes used by providers
                for key in ("text", "value"):
                    v = _get(pd, key)
                    if isinstance(v, str) and v.strip() and v not in candidates:
                        candidates.append(v)

    # 2) liteLLM convenience field
    if isinstance(resp, dict):
        v = resp.get("output_text")
    else:
        v = getattr(resp, "output_text", None)
    if isinstance(v, str) and v.strip():
        candidates.append(v)

    # 3) Legacy Chat Completions fallbacks
    base = resp if isinstance(resp, dict) else getattr(resp, "__dict__", {})
    choices = base.get("choices")
    if isinstance(choices, list) and choices:
        ch0 = choices[0]
        if isinstance(ch0, dict):
            msg = ch0.get("message") or {}
            t = msg.get("content")
            if isinstance(t, str) and t.strip():
                candidates.append(t)
            t2 = ch0.get("text")
            if isinstance(t2, str) and t2.strip():
                candidates.append(t2)

    # 4) Other top-level text fields occasionally surfaced as dict or str
    top_text = None
    if isinstance(base.get("text"), dict):
        top_text = base["text"].get("value") or base["text"].get("text")
    elif isinstance(base.get("text"), str):
        top_text = base["text"]
    if isinstance(top_text, str) and top_text.strip():
        candidates.append(top_text)

    # Deduplicate preserving order
    seen, deduped = set(), []
    for s in candidates:
        if s not in seen:
            seen.add(s)
            deduped.append(s)
    return deduped


def _maybe_json(obj: Any) -> Any:
    if isinstance(obj, str):
        s = obj.strip()
        if s.startswith("{") or s.startswith("["):
            try:
                return json.loads(s)
            except Exception:
                return obj
    return obj


def _extract_structured(resp: Any, *, validator: Optional[TypeAdapter] = None) -> Any:
    """
    Extract structured output from LiteLLM Responses responses.

    Priority:
      1) Top-level: resp.output_parsed
      2) Per-item:  output[i].parsed / .json
      3) Per-part:  output[i].content[j].parsed / .json / .data
      4) Text fallbacks: parse JSON from message content parts (output_text/text),
         skipping 'reasoning' items.
      5) Legacy: choices[0].message.content / choices[0].text

    If `validator` is provided, we prefer the first candidate that validates.
    Otherwise, the first successfully parsed JSON candidate is returned.
    """

    # 1) top-level parsed
    top = resp.get("output_parsed") if isinstance(resp, dict) else getattr(resp, "output_parsed", None)
    if top is not None:
        return top

    # 2 & 3) iterate items and parts
    for item in _iter_output_items(resp):
        # item-level parsed/json
        for key in ("parsed", "json"):
            if key in item and item[key] is not None:
                cand = _maybe_json(item[key])
                if cand is not None:
                    if validator is None:
                        return cand
                    try:
                        validator.validate_python(cand)
                        return cand
                    except Exception:
                        pass
        # part-level parsed/json/data
        content = _get(item, "content") or []
        if isinstance(content, list):
            for part in content:
                pd = _asdict_shallow(part) or part
                for key in ("parsed", "json", "data"):
                    if key in pd and pd[key] is not None:
                        cand = _maybe_json(pd[key])
                        if cand is not None:
                            if validator is None:
                                return cand
                            try:
                                validator.validate_python(cand)
                                return cand
                            except Exception:
                                pass

    # 4) text fallbacks
    candidates = _collect_text_parts(resp)
    parsed_candidates: List[Any] = []
    for s in candidates:
        cand = _parse_json_from_text(s)
        if cand is not None:
            if validator is None:
                return cand
            try:
                validator.validate_python(cand)
                return cand
            except Exception:
                parsed_candidates.append(cand)

    # 5) If nothing validated, but we parsed something, return the first for logging
    return parsed_candidates[0] if parsed_candidates else None


# ------------------------- payload / schema helpers -------------------------

REASONING_MODEL_PREFIXES = ("gpt-5", "o3", "o4-mini-deep-research")


def _is_reasoning_model(model: Optional[str]) -> bool:
    m = (model or "").lower()
    return m.startswith(REASONING_MODEL_PREFIXES)


def _messages_to_input(messages: Any) -> List[Dict[str, Any]]:
    """Normalize messages into the Responses API `input` array."""
    out: List[Dict[str, Any]] = []
    try:
        for m in messages or []:
            if isinstance(m, dict) and "role" in m and "content" in m:
                out.append({"role": m["role"], "content": m.get("content") or ""})
                continue
            role = getattr(m, "type", None) or getattr(m, "role", None) or "user"
            if role == "human":
                role = "user"
            if role == "ai":
                role = "assistant"
            content = getattr(m, "content", None)
            if content is None and isinstance(m, dict):
                content = m.get("content")
            out.append({"role": role, "content": content or ""})
    except Exception:
        out = [{"role": "user", "content": str(messages)[:4000]}]
    return out


def _schema_envelope(name: str, schema: Dict[str, Any]) -> Dict[str, Any]:
    safe = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in (name or "response"))[:64] or "response"
    return {
        "type": "json_schema",
        "json_schema": {
            "name": safe,
            "strict": True,
            "schema": schema,
        },
    }


def _build_response_format(*, tool_name: str, response_model: Any, response_format: Optional[dict]) -> Optional[dict]:
    """Prefer explicit Responses API envelopes; otherwise derive from Pydantic TypeAdapter/model."""
    if isinstance(response_format, dict) and response_format.get("type") == "json_schema":
        return response_format
    if isinstance(response_format, dict) and "schema" in response_format:
        return _schema_envelope(response_format.get("name") or tool_name or "response", response_format["schema"])
    try:
        adapter = response_model if isinstance(response_model, TypeAdapter) else TypeAdapter(response_model)
        schema = adapter.json_schema()
        name = schema.get("title") or getattr(response_model, "__name__", tool_name) or "response"
        return _schema_envelope(name, schema)
    except Exception:
        return None


def _apply_text_params_top_level(payload: Dict[str, Any], text_params: Optional[Dict[str, Any]]) -> None:
    if not text_params:
        return
    for k in ("temperature", "top_p", "frequency_penalty", "presence_penalty", "seed"):
        if k in text_params and text_params[k] is not None:
            payload[k] = text_params[k]


# ---------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------

class LLMService:
    """
    Resilient structured-output wrapper around LiteLLM Responses API.

    - Always sends `response_format` with strict JSON Schema when a model/schema
      is provided, enabling structured outputs.
    - Extracts JSON from multiple locations with robust fallbacks.
    - Tolerates object-like content parts (fixes the original bug).
    """

    def __init__(self) -> None:
        self.default_model = getattr(settings, "LLM_MODEL_DEFAULT", "gpt-5-mini")
        self.default_timeout_s = int(getattr(settings, "LLM_REQUEST_TIMEOUT_S", 60))
        self.default_max_output_tokens = int(getattr(settings, "LLM_MAX_OUTPUT_TOKENS", 2048))
        self.text_defaults = getattr(settings, "LLM_TEXT_DEFAULT", None) or {}
        self.reasoning_defaults = getattr(settings, "LLM_REASONING_DEFAULT", None) or {}

    async def get_structured_response(
        self,
        *,
        tool_name: str,
        messages: Any,
        response_model: Any,
        response_format: Optional[dict] = None,
        trace_id: Optional[str] = None,
        session_id: Optional[str] = None,
        model: Optional[str] = None,
        max_output_tokens: Optional[int] = None,
        timeout_s: Optional[int] = None,
        text_params: Optional[Dict[str, Any]] = None,
        reasoning: Optional[Dict[str, Any]] = None,
        truncation: Optional[str] = None,
        tool_choice: Optional[Union[str, Dict[str, Any]]] = None,  # ignored for structured calls
        metadata: Optional[Dict[str, Any]] = None,
    ):
        mdl = model or self.default_model
        rf = _build_response_format(tool_name=tool_name, response_model=response_model, response_format=response_format)
        if rf is None:
            logger.error("llm.structured.schema.missing", tool=tool_name, model=mdl)
            raise StructuredOutputError(
                "Structured call requires a valid JSON Schema. Provide an explicit `response_format` JSON Schema or a Pydantic model/TypeAdapter."
            )

        payload: Dict[str, Any] = {
            "model": mdl,
            "input": _messages_to_input(messages),
            "max_output_tokens": int(max_output_tokens or self.default_max_output_tokens),
            "timeout": int(timeout_s or self.default_timeout_s),
            # Strict structured call: disallow tool calls in this envelope.
            "tool_choice": "none",
            "response_format": rf,
            "metadata": {
                k: v
                for k, v in {
                    "tool": tool_name,
                    "trace_id": trace_id,
                    "session_id": session_id,
                    **(metadata or {}),
                }.items()
                if v is not None
            },
        }

        if truncation:
            payload["truncation"] = truncation

        if _is_reasoning_model(mdl):
            call_effort = (reasoning or {}).get("effort") if isinstance(reasoning, dict) else None
            merged = {**self.reasoning_defaults, **({"effort": call_effort} if call_effort is not None else {})}
            if merged:
                payload["reasoning"] = merged
        else:
            merged = {**self.text_defaults, **(text_params or {})}
            _apply_text_params_top_level(payload, merged)

        try:
            resp = await asyncio.to_thread(litellm.responses, **payload)
        except Exception as e:
            logger.error(
                "llm.structured.call.fail",
                error=str(e),
                model=mdl,
                tool=tool_name,
                trace_id=trace_id,
                session_id=session_id,
            )
            raise

        # Raw response log for debugging provider drift
        try:
            raw_dict = getattr(resp, "__dict__", None) or (resp if isinstance(resp, dict) else None)
            logger.info(
                "llm.raw_response.received",
                model=mdl,
                tool=tool_name,
                trace_id=trace_id,
                session_id=session_id,
                response_id=(getattr(resp, "id", None) if not isinstance(resp, dict) else resp.get("id")),
                raw_response=coerce_json(raw_dict or resp),
            )
        except Exception:
            logger.info("llm.raw_response.received", model=mdl, tool=tool_name)

        # Prefer candidates that validate against the desired schema
        validator: Optional[TypeAdapter] = None
        try:
            validator = response_model if isinstance(response_model, TypeAdapter) else TypeAdapter(response_model)
        except Exception:
            validator = None

        parsed = _extract_structured(resp, validator=validator)
        if parsed is None:
            # Produce a useful preview for debugging
            try:
                as_dict = getattr(resp, "__dict__", None) or (resp if isinstance(resp, dict) else None)
                preview = _shape_preview(as_dict or resp)
            except Exception:
                preview = "<unavailable>"
            logger.error(
                "llm.structured.parse.fail",
                model=mdl,
                tool=tool_name,
                response_id=(getattr(resp, "id", None) if not isinstance(resp, dict) else resp.get("id")),
                preview=preview,
            )
            raise StructuredOutputError(
                "Responses API returned no structured output. Could not locate/parse JSON.", preview=preview
            )

        # Validate & coerce into the requested model
        try:
            if isinstance(response_model, TypeAdapter):
                return response_model.validate_python(parsed)
            if hasattr(response_model, "model_validate"):
                return response_model.model_validate(parsed)
            adapter = TypeAdapter(response_model)
            return adapter.validate_python(parsed)
        except ValidationError as ve:
            preview = _shape_preview(parsed)
            logger.error(
                "llm.structured.validation.fail",
                tool=tool_name,
                model=mdl,
                err=str(ve),
                sample=preview,
            )
            raise StructuredOutputError("Structured output validation failed.", preview=preview) from ve


# Singleton
llm_service = LLMService()
