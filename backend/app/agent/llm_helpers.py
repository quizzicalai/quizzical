# backend/app/agent/llm_helpers.py

from __future__ import annotations
from typing import Any, Optional, Union

import time
import structlog
from pydantic import BaseModel
from pydantic.type_adapter import TypeAdapter

from app.core.config import settings
from app.services.llm_service import llm_service

logger = structlog.get_logger(__name__)
ModelLike = Union[dict, TypeAdapter, type, BaseModel]


def _safe_len(x) -> Optional[int]:
    try:
        return len(x)
    except Exception:
        return None


def _cfg_get(cfg: Any, key: str, default: Any = None) -> Any:
    if cfg is None:
        return default
    try:
        if isinstance(cfg, dict):
            return cfg.get(key, default)
        return getattr(cfg, key, default)
    except Exception:
        return default


def _deep_get(obj: Any, path: list[str], default=None):
    cur = obj
    for p in path:
        if cur is None:
            return default
        try:
            cur = cur[p] if isinstance(cur, dict) else getattr(cur, p, None)
        except Exception:
            return default
    return cur if cur is not None else default


def _get_tool_cfg(tool_name: str) -> dict | None:
    """
    Support all of these:
      - settings.llm_tools[tool_name]
      - settings.llm.tools[tool_name]
      - settings.quizzical.llm.tools[tool_name]   (matches your YAML)
    """
    for path in (
        ["llm_tools", tool_name],
        ["llm", "tools", tool_name],
        ["quizzical", "llm", "tools", tool_name],
    ):
        cfg = _deep_get(settings, path, None)
        if cfg:
            return cfg
    return None


async def invoke_structured(
    *,
    tool_name: str,
    messages: Any,
    response_model: ModelLike,
    schema_kwargs: Optional[dict] = None,
    explicit_schema: Optional[dict] = None,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
):
    if schema_kwargs:
        try:
            logger.debug(
                "llm.invoke_structured.schema_kwargs.ignored",
                tool=tool_name,
                keys=list(schema_kwargs.keys()),
                trace_id=trace_id,
                session_id=session_id,
            )
        except Exception:
            pass

    cfg = _get_tool_cfg(tool_name) or {}
    model = _cfg_get(cfg, "model")
    max_tokens = _cfg_get(cfg, "max_output_tokens")
    timeout_s = _cfg_get(cfg, "timeout_s")
    temperature = _cfg_get(cfg, "temperature")
    effort = _cfg_get(cfg, "effort")
    tool_choice = _cfg_get(cfg, "tool_choice")

    text_params = {"temperature": temperature} if (temperature is not None) else None
    reasoning = {"effort": effort} if (effort is not None) else None

    logger.info(
        "llm.tool.config",
        tool=tool_name,
        model=model,
        temp=temperature,
        max_tokens=max_tokens,
        timeout_s=timeout_s,
    )

    t0 = time.perf_counter()
    try:
        result = await llm_service.get_structured_response(
            tool_name=tool_name,
            messages=messages,
            response_model=response_model,
            response_format=explicit_schema,
            trace_id=trace_id,
            session_id=session_id,
            model=model,
            max_output_tokens=max_tokens,
            timeout_s=timeout_s,
            text_params=text_params,
            reasoning=reasoning,
            tool_choice=tool_choice,
        )

        try:
            logger.info(
                "llm.invoke_structured.ok",
                tool=tool_name,
                latency_ms=round((time.perf_counter() - t0) * 1000.0, 1),
                trace_id=trace_id,
                session_id=session_id,
                has_explicit_schema=bool(explicit_schema),
                messages_count=_safe_len(messages),
                response_model_type=type(response_model).__name__,
            )
        except Exception:
            pass

        # If they asked for a BaseModel class, sanity check instance type
        try:
            if isinstance(response_model, type) and issubclass(response_model, BaseModel):
                assert isinstance(result, response_model), "Result not instance of requested BaseModel."
        except Exception:
            pass

        return result

    except Exception as e:
        logger.error(
            "llm.invoke_structured.fail",
            error=str(e),
            tool=tool_name,
            trace_id=trace_id,
            session_id=session_id,
            has_explicit_schema=bool(explicit_schema),
            messages_count=_safe_len(messages),
            response_model_type=type(response_model).__name__,
        )
        raise
