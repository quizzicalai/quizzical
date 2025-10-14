# backend/app/agent/llm_helpers.py
from __future__ import annotations

import time
from typing import Any, Optional, Union

import structlog
from pydantic import BaseModel
from pydantic.type_adapter import TypeAdapter

from app.services.llm_service import llm_service

logger = structlog.get_logger(__name__)

ModelLike = Union[dict, TypeAdapter, type, BaseModel]


def _safe_len(x) -> Optional[int]:
    try:
        return len(x)  # type: ignore[arg-type]
    except Exception:
        return None


async def invoke_structured(
    *,
    tool_name: str,
    messages,
    response_model: ModelLike,
    schema_kwargs: Optional[dict] = None,
    explicit_schema: Optional[dict] = None,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
):
    """
    Single path to call the LLM with structured outputs.
    - If `explicit_schema` is provided, it is used.
    - Else, pass the `response_model` through. The llm_service will:
        • wrap JSON Schema envelopes with response_format={"type":"json_schema",...}
        • or derive a JSON Schema from Pydantic/TypeAdapter and set strict=True.
    Returns a validated Pydantic object when response_model is a Pydantic model.

    Logging:
    - Success: llm.invoke_structured.ok (latency_ms, tool, trace_id/session_id)
    - Failure: llm.invoke_structured.fail (error, tool, trace_id/session_id)
    """
    if schema_kwargs:
        # Not used by current pipeline; keep visibility without failing.
        try:
            logger.debug(
                "llm.invoke_structured.schema_kwargs.ignored",
                tool=tool_name,
                keys=list(schema_kwargs.keys()),
                trace_id=trace_id,
                session_id=session_id,
            )
        except Exception:
            # Best-effort logging; do not block the call
            pass

    t0 = time.perf_counter()
    try:
        schema = explicit_schema  # json_schema envelope or None
        result = await llm_service.get_structured_response(
            tool_name=tool_name,
            messages=messages,
            response_model=response_model,
            response_format=explicit_schema,
            trace_id=trace_id,
            session_id=session_id,
        )

        # Success telemetry (PII-safe)
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

        return result

    except Exception as e:
        # Surface structured context while avoiding prompt content
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
