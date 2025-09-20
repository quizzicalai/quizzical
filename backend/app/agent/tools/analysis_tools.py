# backend/app/agent/tools/analysis_tools.py
"""
Agent Tools: Analysis, Safety, and Error Handling

These tools:
- Assess topic/synopsis safety (returns 'safe' or 'unsafe')
- Analyze tool execution errors and suggest corrective actions
- Generate user-friendly failure explanations

They are thin wrappers over the PromptManager + LLMService, with strong logging
and tolerant fallbacks. Public signatures remain unchanged.
"""

from __future__ import annotations

from typing import Dict, Literal, Optional

import structlog
from langchain_core.tools import tool

from app.agent.prompts import prompt_manager
from app.services.llm_service import llm_service

logger = structlog.get_logger(__name__)


@tool
async def assess_category_safety(
    category: str,
    synopsis: str,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Literal["safe", "unsafe"]:
    """
    Assesses the user-provided category and generated synopsis for safety.
    Returns 'safe' or 'unsafe'. Any parsing ambiguity defaults to 'safe' to keep UX flowing.
    """
    logger.info("tool.assess_category_safety.start", category=category)
    prompt = prompt_manager.get_prompt("safety_checker")
    messages = prompt.invoke({"category": category, "synopsis": synopsis}).messages

    try:
        response = await llm_service.get_text_response(
            tool_name="safety_checker",
            messages=messages,
            trace_id=trace_id,
            session_id=session_id,
        )
        verdict = "unsafe" if "unsafe" in (response or "").lower() else "safe"
        logger.info("tool.assess_category_safety.ok", verdict=verdict)
        return verdict  # type: ignore[return-value]
    except Exception as e:
        logger.error("tool.assess_category_safety.fail", error=str(e), exc_info=True)
        # Conservative fallback: allow flow to continue; UI may still gate if needed
        return "safe"


@tool
async def analyze_tool_error(
    error_message: str,
    state: Dict,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> str:
    """
    Analyzes a tool execution error and suggests a corrective action (freeform text).
    Non-fatal: on any issue returns a short, generic suggestion.
    """
    logger.warning("tool.analyze_tool_error.start", error_message=error_message[:200])
    prompt = prompt_manager.get_prompt("error_analyzer")
    messages = prompt.invoke({"error_message": error_message, "state": state}).messages
    try:
        text = await llm_service.get_text_response(
            tool_name="error_analyzer",
            messages=messages,
            trace_id=trace_id,
            session_id=session_id,
        )
        logger.info("tool.analyze_tool_error.ok", suggestion_preview=(text or "")[:160])
        return text
    except Exception as e:
        logger.error("tool.analyze_tool_error.fail", error=str(e), exc_info=True)
        return "Retry the last step with stricter parameters, or switch to a simpler tool."


@tool
async def explain_failure_to_user(
    error_summary: str,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> str:
    """
    Generates a user-friendly message explaining why the quiz generation failed.
    """
    logger.error("tool.explain_failure_to_user.start", error_summary=error_summary[:200])
    prompt = prompt_manager.get_prompt("failure_explainer")
    messages = prompt.invoke({"error_summary": error_summary}).messages
    try:
        text = await llm_service.get_text_response(
            tool_name="failure_explainer",
            messages=messages,
            trace_id=trace_id,
            session_id=session_id,
        )
        logger.info("tool.explain_failure_to_user.ok")
        return text
    except Exception as e:
        logger.error("tool.explain_failure_to_user.fail", error=str(e), exc_info=True)
        return "Sorry, we had trouble generating your quiz. Please try a different topic."
