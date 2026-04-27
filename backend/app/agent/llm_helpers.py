# backend/app/agent/llm_helpers.py

from __future__ import annotations

import time
from typing import Any, Optional, Union

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


# ---------------------------------------------------------------------------
# Adaptive model tier resolution (§7.7.3)
# ---------------------------------------------------------------------------

# Tools whose model tier swaps based on ``state.topic_knowledge.is_well_known``.
# (Keep in sync with backend-design.MD §7.7.3 / AC-AGENT-TIER-2..8.)
#
# Phase 7 expansion (AC-AGENT-TIER-4): every tool that materially shapes the
# user-visible artifact (synopsis, archetype list, character profiles, questions,
# final result) participates in adaptive tiering so fringe topics can opt into
# higher-fidelity ``model_unknown`` while well-known topics stay on the cheap/fast
# default. Operators upgrade by setting ``model_unknown`` per-tool in
# ``appconfig.local.yaml``; absence falls back to ``model`` silently.
ADAPTIVE_TIER_TOOLS: frozenset[str] = frozenset({
    "initial_planner",
    "synopsis_generator",
    "character_list_generator",
    "profile_writer",
    "profile_batch_writer",
    "profile_improver",
    "question_generator",
    "next_question_generator",
    "final_profile_writer",
})


def resolve_model_for_tool(tool_name: str, *, is_well_known: bool) -> Optional[str]:
    """Return the model string a given tool should use for this run.

    For tools in :data:`ADAPTIVE_TIER_TOOLS`:
      - well-known topic → ``cfg["model"]`` (Flash tier)
      - fringe topic     → ``cfg["model_unknown"]`` (Pro / 3.x tier),
        falling back to ``cfg["model"]`` if not set (AC-AGENT-TIER-3).

    For all other tools, always returns ``cfg["model"]``.
    Returns ``None`` if the tool has no config entry.
    """
    cfg = _get_tool_cfg(tool_name)
    if not cfg:
        return None
    base = _cfg_get(cfg, "model")
    if tool_name in ADAPTIVE_TIER_TOOLS and not is_well_known:
        upgraded = _cfg_get(cfg, "model_unknown")
        if upgraded:
            return upgraded
    return base


async def invoke_structured(
    *,
    tool_name: str,
    messages: Any,
    response_model: ModelLike,
    explicit_schema: Optional[dict] = None,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
):
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

        # If they asked for a BaseModel class, enforce instance type with a
        # clear TypeError instead of swallowing the mismatch silently.
        if (
            isinstance(response_model, type)
            and issubclass(response_model, BaseModel)
            and not isinstance(result, response_model)
        ):
            raise TypeError(
                f"invoke_structured: expected {response_model.__name__} instance, "
                f"got {type(result).__name__}"
            )

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
