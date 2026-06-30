# backend/app/services/llm_service.py
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging as _logging
import os
import re
from collections.abc import Iterable
from typing import Any

import litellm
import structlog
from pydantic import ValidationError
from pydantic.type_adapter import TypeAdapter

from app.core.config import settings
from app.services.retry import retry_async

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

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------

class StructuredOutputError(RuntimeError):
    """Raised when we cannot extract/validate structured output."""

    def __init__(self, message: str, *, preview: str | None = None) -> None:
        super().__init__(message)
        self.preview = preview


class LLMResponseTooLargeError(RuntimeError):
    """§9.7.6 — Raised when an LLM response exceeds ``settings.llm.max_response_bytes``.

    The cap defends against a buggy or compromised provider returning an
    unreasonably large payload that would exhaust memory or stall parsing.
    """

    def __init__(self, *, size_bytes: int, max_bytes: int) -> None:
        super().__init__(
            f"LLM response too large: {size_bytes} bytes > cap {max_bytes} bytes"
        )
        self.size_bytes = size_bytes
        self.max_bytes = max_bytes


def _enforce_response_size_cap(
    *,
    raw_dict: Any,
    resp: Any,
    model: str,
    tool: str | None,
    trace_id: str | None,
    session_id: str | None,
) -> None:
    """§9.7.6 AC-LLM-SIZE-1..3 — raise if serialised payload exceeds the cap.

    Measures against the JSON-serialised payload so it covers both SDK-object
    and dict response shapes. Fails open on instrumentation errors (cannot
    measure → do not block).
    """
    try:
        max_bytes = int(
            getattr(getattr(settings, "llm", None), "max_response_bytes", 262144)
            or 262144
        )
    except Exception:
        max_bytes = 262144
    try:
        measured = raw_dict if raw_dict is not None else resp
        size = len(json.dumps(coerce_json(measured), default=str).encode("utf-8"))
    except Exception:
        return
    if size and size > max_bytes:
        logger.error(
            "llm.response.too_large",
            model=model,
            tool=tool,
            trace_id=trace_id,
            session_id=session_id,
            size_bytes=size,
            max_bytes=max_bytes,
        )
        raise LLMResponseTooLargeError(size_bytes=size, max_bytes=max_bytes)


# ---------------------------------------------------------------------
# §16.1 — Transient-error classification for LLM retry
# ---------------------------------------------------------------------

# Tuple of LiteLLM exception classes treated as transient. Resolved
# defensively because LiteLLM versions differ in available subclasses.
_LITELLM_TRANSIENT_CLASSES: tuple[type[BaseException], ...] = tuple(
    cls for cls in (
        getattr(litellm, "Timeout", None),
        getattr(litellm, "APIConnectionError", None),
        getattr(litellm, "RateLimitError", None),
        getattr(litellm, "InternalServerError", None),
        getattr(litellm, "ServiceUnavailableError", None),
        getattr(litellm, "BadGatewayError", None),
    ) if isinstance(cls, type)
)


def _is_llm_transient(exc: BaseException) -> bool:
    """AC-LLM-RETRY-1/2 — return True iff ``exc`` is worth retrying.

    Programmer errors (BadRequest, Auth, schema validation) are explicitly
    excluded so we never paper over a bug with a retry loop.
    """
    if isinstance(exc, asyncio.TimeoutError):
        return True
    if _LITELLM_TRANSIENT_CLASSES and isinstance(exc, _LITELLM_TRANSIENT_CLASSES):
        return True
    return False


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


def _asdict_shallow(obj: Any) -> dict[str, Any] | None:
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


def _extract_balanced_block(s: str, start: int, opener: str, closer: str) -> str | None:
    """
    Scan string `s` starting at `start` for the matching `closer`, handling nested
    pairs and string escaping.
    """
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


def _find_first_balanced_json(s: str) -> str | None:
    """
    Find the first balanced JSON object/array substring in s.
    Returns the substring or None.
    """
    s = s.strip()
    if not s:
        return None
    # Fast path
    if s[0] in "[{":
        return s

    pairs: list[tuple[str, str]] = [("{", "}"), ("[", "]")]
    for opener, closer in pairs:
        start = s.find(opener)
        if start != -1:
            chunk = _extract_balanced_block(s, start, opener, closer)
            if chunk:
                return chunk
    return None


def _parse_json_from_text(s: str) -> Any | None:
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

DefItem = dict[str, Any]


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


def _harvest_responses_api_text(resp: Any) -> list[str]:
    """Collect text from standard Responses API output items."""
    candidates: list[str] = []
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
                # Fallback fields
                for key in ("text", "value"):
                    v = _get(pd, key)
                    if isinstance(v, str) and v.strip() and v not in candidates:
                        candidates.append(v)
    return candidates


def _harvest_legacy_text(resp: Any) -> list[str]:
    """Collect text from legacy Chat Completions style choices."""
    candidates: list[str] = []
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
    return candidates


def _collect_text_parts(resp: Any) -> list[str]:
    """
    Collect candidate text blobs, in order of likelihood:
      - output[].content[].text (Responses API)
      - top-level `output_text` (LiteLLM convenience)
      - choices[0].message.content (Legacy)
      - other top-level text fields
    """
    candidates: list[str] = []

    # 1) Responses API path
    candidates.extend(_harvest_responses_api_text(resp))

    # 2) liteLLM convenience field
    val = resp.get("output_text") if isinstance(resp, dict) else getattr(resp, "output_text", None)
    if isinstance(val, str) and val.strip():
        candidates.append(val)

    # 3) Legacy Chat Completions fallbacks
    candidates.extend(_harvest_legacy_text(resp))

    # 4) Other top-level text fields
    base = resp if isinstance(resp, dict) else getattr(resp, "__dict__", {})
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


def _try_validate(validator: TypeAdapter | None, cand: Any) -> Any | None:
    """Helper to check if a candidate passes validation, if validator is present."""
    if validator is None:
        return cand
    try:
        validator.validate_python(cand)
        return cand
    except Exception:
        return None


def _check_keys_in_dict(d: Any, keys: Iterable[str], validator: TypeAdapter | None) -> Any | None:
    """Helper to check specific keys in a dict-like object for valid JSON."""
    for key in keys:
        if key in d and d[key] is not None:
            cand = _maybe_json(d[key])
            if cand is not None:
                valid = _try_validate(validator, cand)
                if valid is not None:
                    return valid
    return None


def _scan_items_for_preparsed_json(resp: Any, validator: TypeAdapter | None) -> Any | None:
    """Scan output items for existing 'parsed' or 'json' objects."""
    for item in _iter_output_items(resp):
        # item-level
        found = _check_keys_in_dict(item, ("parsed", "json"), validator)
        if found is not None:
            return found

        # part-level
        content = _get(item, "content") or []
        if isinstance(content, list):
            for part in content:
                pd = _asdict_shallow(part) or part
                found_part = _check_keys_in_dict(pd, ("parsed", "json", "data"), validator)
                if found_part is not None:
                    return found_part
    return None


def _extract_structured(resp: Any, *, validator: TypeAdapter | None = None) -> Any:
    """
    Extract structured output from LiteLLM Responses responses.
    """
    # 1) top-level parsed
    top = resp.get("output_parsed") if isinstance(resp, dict) else getattr(resp, "output_parsed", None)
    if top is not None:
        return top

    # 2 & 3) iterate items and parts for pre-parsed JSON
    found = _scan_items_for_preparsed_json(resp, validator)
    if found is not None:
        return found

    # 4) text fallbacks
    candidates = _collect_text_parts(resp)
    parsed_candidates: list[Any] = []
    for s in candidates:
        cand = _parse_json_from_text(s)
        if cand is not None:
            valid = _try_validate(validator, cand)
            if valid is not None:
                return valid
            parsed_candidates.append(cand)

    # 5) If nothing validated, but we parsed something, return the first for logging
    return parsed_candidates[0] if parsed_candidates else None


# ------------------------- payload / schema helpers -------------------------

REASONING_MODEL_PREFIXES = ("gpt-5", "o3", "o4-mini-deep-research")


def _is_reasoning_model(model: str | None) -> bool:
    m = (model or "").lower()
    return m.startswith(REASONING_MODEL_PREFIXES)


# AC-PROD-R11-INFRA-2 — provider-key fallback. Map "needs key X" → fallback model.
# Used when the requested model's provider has no API key in the env (e.g. the
# prod Container App is mid-rollout and OPENAI_API_KEY isn't wired yet).
_PROVIDER_FALLBACK_MODEL = "gemini/gemini-flash-latest"


def _provider_of(model: str | None) -> str:
    """Coarse provider family for a model string (used to pick a CROSS-provider
    fallback so a single-provider incident is actually escaped)."""
    ml = (model or "").lower()
    if ml.startswith(("gpt-", "openai/", "o3", "o4")):
        return "openai"
    if ml.startswith(("gemini", "gemini/", "google/")):
        return "gemini"
    if ml.startswith(("anthropic/", "claude-")):
        return "anthropic"
    if ml.startswith("groq/"):
        return "groq"
    return "other"


def _default_cross_provider_fallback(model: str | None) -> str | None:
    """Hitlist #4 — derive a CROSS-provider runtime fallback for ``model`` when a
    tool does not configure ``fallback_model`` explicitly.

    Mirrors :data:`_PROVIDER_FALLBACK_MODEL`: the critical path is OpenAI
    (gpt-4o-mini), so an OpenAI terminal error fails over to Gemini Flash. For a
    primary that is ALREADY Gemini we fail over to OpenAI's cheap tier so the two
    providers back each other up. Returns ``None`` when we can't pick a different
    provider (so the caller simply does not attempt a fallback)."""
    provider = _provider_of(model)
    if provider == "openai":
        return _PROVIDER_FALLBACK_MODEL  # gemini/gemini-flash-latest
    if provider == "gemini":
        return "gpt-4o-mini"
    return None


def _substitute_model_if_key_missing(model: str, *, tool_name: str | None = None) -> str:
    """Return ``model`` unchanged when its provider key is present.

    When the provider key is missing we substitute :data:`_PROVIDER_FALLBACK_MODEL`
    so the call doesn't fail with an opaque auth error. We log at WARNING so
    operators see the substitution clearly.
    """
    ml = (model or "").lower()
    needs_key: tuple[str, str] | None = None
    if ml.startswith(("gpt-", "openai/", "o3", "o4")):
        needs_key = ("OPENAI_API_KEY", "openai")
    elif ml.startswith("groq/"):
        needs_key = ("GROQ_API_KEY", "groq")
    elif ml.startswith(("anthropic/", "claude-")):
        needs_key = ("ANTHROPIC_API_KEY", "anthropic")
    if needs_key is None:
        return model
    env_var, provider = needs_key
    if os.getenv(env_var):
        return model
    logger.warning(
        "llm.model.fallback",
        requested_model=model,
        fallback_model=_PROVIDER_FALLBACK_MODEL,
        reason=f"{env_var} not set",
        provider=provider,
        tool=tool_name,
    )
    return _PROVIDER_FALLBACK_MODEL


def _messages_to_input(messages: Any) -> list[dict[str, Any]]:
    """Normalize messages into the Responses API `input` array."""
    out: list[dict[str, Any]] = []
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


def _schema_envelope(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    safe = "".join(ch if ch.isalnum() or ch in "_-" else "_" for ch in (name or "response"))[:64] or "response"
    return {
        "type": "json_schema",
        "json_schema": {
            "name": safe,
            "strict": True,
            "schema": schema,
        },
    }


def _build_response_format(*, tool_name: str, response_model: Any, response_format: dict | None) -> dict | None:
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


def _apply_text_params_top_level(payload: dict[str, Any], text_params: dict[str, Any] | None) -> None:
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

    def _build_litellm_payload(
        self,
        model: str,
        messages: Any,
        rf: dict[str, Any],
        max_output_tokens: int | None,
        timeout_s: int | None,
        truncation: str | None,
        text_params: dict | None,
        reasoning: dict | None,
        metadata: dict,
        tool_name: str,
        trace_id: str | None,
        session_id: str | None,
        cache: bool | None = None,
    ) -> dict[str, Any]:
        """Constructs the request dictionary for LiteLLM.

        ``cache`` follows §9.7.8 (AC-LLM-CACHE-4/5):
          - ``True``  → metadata gets ``"caching": True`` (opt in for this call).
          - ``False`` → metadata gets ``"no-cache": True`` (force fresh call).
          - ``None``  → no cache-related metadata is added.
        """
        cache_meta: dict[str, Any] = {}
        if cache is True:
            cache_meta["caching"] = True
        elif cache is False:
            cache_meta["no-cache"] = True

        payload: dict[str, Any] = {
            "model": model,
            "input": _messages_to_input(messages),
            "max_output_tokens": int(max_output_tokens or self.default_max_output_tokens),
            "timeout": int(timeout_s or self.default_timeout_s),
            "tool_choice": "none",
            "response_format": rf,
            "metadata": {
                k: v
                for k, v in {
                    "tool": tool_name,
                    "trace_id": trace_id,
                    "session_id": session_id,
                    **(metadata or {}),
                    **cache_meta,
                }.items()
                if v is not None
            },
        }

        if truncation:
            payload["truncation"] = truncation

        if _is_reasoning_model(model):
            call_effort = (reasoning or {}).get("effort") if isinstance(reasoning, dict) else None
            merged = {**self.reasoning_defaults, **({"effort": call_effort} if call_effort is not None else {})}
            if merged:
                payload["reasoning"] = merged
        else:
            merged_txt = {**self.text_defaults, **(text_params or {})}
            _apply_text_params_top_level(payload, merged_txt)

        return payload

    async def get_structured_response(  # noqa: C901  (linear flow: payload build + retry + size cap + parse + validate guards)
        self,
        *,
        tool_name: str,
        messages: Any,
        response_model: Any,
        response_format: dict | None = None,
        trace_id: str | None = None,
        session_id: str | None = None,
        model: str | None = None,
        fallback_model: str | None = None,
        max_output_tokens: int | None = None,
        timeout_s: int | None = None,
        text_params: dict[str, Any] | None = None,
        reasoning: dict[str, Any] | None = None,
        truncation: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,  # ignored for structured calls
        metadata: dict[str, Any] | None = None,
        cache: bool | None = None,
    ):
        # §17.1 (AC-SCALE-LLM-1..3) — bound process-wide LLM concurrency.
        from app.services.llm_concurrency import get_global_limiter

        limiter = get_global_limiter()
        async with limiter.acquire(tool=tool_name):
            return await self._do_structured_response(
                tool_name=tool_name,
                messages=messages,
                response_model=response_model,
                response_format=response_format,
                trace_id=trace_id,
                session_id=session_id,
                model=model,
                fallback_model=fallback_model,
                max_output_tokens=max_output_tokens,
                timeout_s=timeout_s,
                text_params=text_params,
                reasoning=reasoning,
                truncation=truncation,
                tool_choice=tool_choice,
                metadata=metadata,
                cache=cache,
            )

    async def _do_structured_response(
        self,
        *,
        tool_name: str,
        messages: Any,
        response_model: Any,
        response_format: dict | None = None,
        trace_id: str | None = None,
        session_id: str | None = None,
        model: str | None = None,
        fallback_model: str | None = None,
        max_output_tokens: int | None = None,
        timeout_s: int | None = None,
        text_params: dict[str, Any] | None = None,
        reasoning: dict[str, Any] | None = None,
        truncation: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        cache: bool | None = None,
    ):
        """Run a structured call on the primary model with in-provider retries,
        then — ONLY on a TERMINAL provider error (transient class after retries
        are exhausted) — fail over to a CROSS-provider model EXACTLY ONCE.

        Hitlist #4 (2026-06-30): the prior fallback only fired on key-ABSENCE at
        startup; a runtime 429/5xx/timeout exhausted the same-provider retries
        then failed the whole agent run. The single cross-provider retry here
        turns an OpenAI incident into a (slower) Gemini success instead of a
        user-facing failure — bounded to ONE extra attempt so it can never loop
        or double-spend beyond that one retry. The fallback result still
        validates against the same schema.
        """
        primary = _substitute_model_if_key_missing(
            model or self.default_model, tool_name=tool_name
        )
        # Resolve the cross-provider failover target: explicit per-tool
        # ``fallback_model`` wins; otherwise derive a sensible default. An
        # explicit empty string disables failover for this call.
        if fallback_model is None:
            fb = _default_cross_provider_fallback(primary)
        elif fallback_model.strip() == "":
            fb = None
        else:
            fb = _substitute_model_if_key_missing(fallback_model, tool_name=tool_name)
        # Never "fail over" to the same provider (or the same model) — that would
        # not escape a provider incident and would just double-spend.
        if fb is not None and (fb == primary or _provider_of(fb) == _provider_of(primary)):
            fb = None

        common = {
            "tool_name": tool_name,
            "messages": messages,
            "response_model": response_model,
            "response_format": response_format,
            "trace_id": trace_id,
            "session_id": session_id,
            "max_output_tokens": max_output_tokens,
            "timeout_s": timeout_s,
            "text_params": text_params,
            "reasoning": reasoning,
            "truncation": truncation,
            "metadata": metadata,
            "cache": cache,
        }

        try:
            return await self._attempt_for_model(model=primary, **common)
        except Exception as primary_exc:
            # Fail over EXACTLY ONCE, and ONLY for a terminal provider error
            # (transient class). Deterministic errors (schema/validation/
            # programming, e.g. StructuredOutputError / ValidationError) are NOT
            # retried — a different provider can't fix a schema bug, and retrying
            # would just waste a paid call. No fallback configured → re-raise.
            if fb is None or not _is_llm_transient(primary_exc):
                raise
            logger.warning(
                "llm.structured.failover.attempt",
                primary_model=primary,
                fallback_model=fb,
                tool=tool_name,
                trace_id=trace_id,
                session_id=session_id,
                error=str(primary_exc),
            )
            try:
                result = await self._attempt_for_model(model=fb, **common)
            except Exception as fb_exc:
                # Both providers down for this call — emit a terminal-exhaustion
                # metric/log so an OpenAI incident is observable even when the
                # fallback ALSO fails, then surface the FALLBACK error.
                logger.error(
                    "llm.structured.failover.exhausted",
                    primary_model=primary,
                    fallback_model=fb,
                    tool=tool_name,
                    trace_id=trace_id,
                    session_id=session_id,
                    primary_error=str(primary_exc),
                    fallback_error=str(fb_exc),
                    fallback_transient=_is_llm_transient(fb_exc),
                )
                raise
            logger.info(
                "llm.structured.failover.ok",
                primary_model=primary,
                fallback_model=fb,
                tool=tool_name,
                trace_id=trace_id,
                session_id=session_id,
            )
            return result

    async def _attempt_for_model(  # noqa: C901
        self,
        *,
        model: str,
        tool_name: str,
        messages: Any,
        response_model: Any,
        response_format: dict | None = None,
        trace_id: str | None = None,
        session_id: str | None = None,
        max_output_tokens: int | None = None,
        timeout_s: int | None = None,
        text_params: dict[str, Any] | None = None,
        reasoning: dict[str, Any] | None = None,
        truncation: str | None = None,
        metadata: dict[str, Any] | None = None,
        cache: bool | None = None,
    ):
        """One full structured attempt against a SINGLE model: build payload →
        in-provider retry → log → cost-meter → size-cap → parse → validate.

        Raises on failure. A TRANSIENT provider error (after the in-provider
        retries are exhausted) propagates as-is so the caller can decide whether
        to fail over; deterministic parse/validation errors propagate as
        ``StructuredOutputError`` and are NOT failover-eligible.
        """
        mdl = model
        rf = _build_response_format(tool_name=tool_name, response_model=response_model, response_format=response_format)
        if rf is None:
            logger.error("llm.structured.schema.missing", tool=tool_name, model=mdl)
            raise StructuredOutputError(
                "Structured call requires a valid JSON Schema. Provide an explicit `response_format` JSON Schema or a Pydantic model/TypeAdapter."
            )

        payload = self._build_litellm_payload(
            mdl, messages, rf, max_output_tokens, timeout_s, truncation,
            text_params, reasoning, metadata or {}, tool_name, trace_id, session_id,
            cache=cache,
        )

        try:
            retry_cfg = getattr(getattr(settings, "llm", None), "retry", None)
            max_attempts = int(getattr(retry_cfg, "max_attempts", 3)) if retry_cfg else 3
            base_ms = int(getattr(retry_cfg, "base_ms", 200)) if retry_cfg else 200
            cap_ms = int(getattr(retry_cfg, "cap_ms", 2000)) if retry_cfg else 2000

            def _on_retry(attempt: int, exc: BaseException, delay_s: float) -> None:
                logger.warning(
                    "llm.structured.retrying",
                    attempt=attempt,
                    next_delay_s=round(delay_s, 3),
                    error=str(exc),
                    model=mdl,
                    tool=tool_name,
                    trace_id=trace_id,
                    session_id=session_id,
                )

            async def _call() -> Any:
                return await asyncio.to_thread(litellm.responses, **payload)

            resp = await retry_async(
                _call,
                is_transient=_is_llm_transient,
                max_attempts=max_attempts,
                base_ms=base_ms,
                cap_ms=cap_ms,
                on_retry=_on_retry,
            )
            if max_attempts > 1:
                # Best-effort note when a retry path was taken (success).
                # We can't see attempt count from outside; the warning logs
                # above already trace progress. AC-LLM-RETRY-3 is satisfied
                # by the retrying log line + this success line as a pair.
                logger.debug(
                    "llm.structured.call.ok",
                    model=mdl,
                    tool=tool_name,
                    trace_id=trace_id,
                    session_id=session_id,
                )
        except Exception as e:
            logger.error(
                "llm.structured.call.fail",
                error=str(e),
                model=mdl,
                tool=tool_name,
                trace_id=trace_id,
                session_id=session_id,
                transient=_is_llm_transient(e),
            )
            raise

        # Raw response log. The FULL payload is large and was serialized via
        # coerce_json on EVERY call at INFO — inside the LLM-concurrency slot,
        # so it added CPU + log I/O + latency while holding a permit (P1 perf).
        # Keep cheap metadata at INFO; emit the full payload only at DEBUG, and
        # skip the expensive coerce_json entirely unless DEBUG is enabled.
        try:
            raw_dict = getattr(resp, "__dict__", None) or (resp if isinstance(resp, dict) else None)
            logger.info(
                "llm.raw_response.received",
                model=mdl,
                tool=tool_name,
                trace_id=trace_id,
                session_id=session_id,
                response_id=(getattr(resp, "id", None) if not isinstance(resp, dict) else resp.get("id")),
            )
            if _logging.getLogger(__name__).isEnabledFor(_logging.DEBUG):
                logger.debug(
                    "llm.raw_response.payload",
                    model=mdl,
                    tool=tool_name,
                    trace_id=trace_id,
                    raw_response=coerce_json(raw_dict or resp),
                )
        except Exception:
            logger.info("llm.raw_response.received", model=mdl, tool=tool_name)

        # Hitlist #2 — capture real token/$ usage and feed the daily cents
        # breaker. Fully fail-open: cost_meter swallows every error (missing
        # usage / unmapped pricing / Redis down) so a metering fault can never
        # break the live LLM path or double-count. Recorded once per response.
        try:
            from app.services import cost_meter
            await cost_meter.record_llm_cost(
                resp,
                model=mdl,
                tool=tool_name,
                trace_id=trace_id,
                session_id=session_id,
            )
        except Exception:
            pass

        # §9.7.6 AC-LLM-SIZE-1..3 — enforce hard cap on raw response size.
        _enforce_response_size_cap(
            raw_dict=raw_dict,
            resp=resp,
            model=mdl,
            tool=tool_name,
            trace_id=trace_id,
            session_id=session_id,
        )

        # Prepare validator
        validator: TypeAdapter | None = None
        try:
            validator = response_model if isinstance(response_model, TypeAdapter) else TypeAdapter(response_model)
        except Exception:
            validator = None

        parsed = _extract_structured(resp, validator=validator)
        if parsed is None:
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

        # Validate & coerce
        try:
            if validator:
                return validator.validate_python(parsed)
            # Fallback if validator failed creation but we have a parsed dict
            if hasattr(response_model, "model_validate"):
                return response_model.model_validate(parsed)
            return parsed
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
