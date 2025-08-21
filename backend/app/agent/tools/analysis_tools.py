# backend/app/agent/tools/analysis_tools.py
"""
Agent Tools: Analysis, Safety, and Error Handling

This module contains tools that allow the agent to analyze its own performance,
ensure content safety, and handle failures gracefully.
"""
from typing import Dict, Literal, Optional

import structlog
from langchain_core.tools import tool

from app.agent.prompts import prompt_manager
from app.services.llm_service import llm_service

logger = structlog.get_logger(__name__)


@tool
async def assess_category_safety(
    category: str, synopsis: str, trace_id: Optional[str] = None, session_id: Optional[str] = None
) -> Literal["safe", "unsafe"]:
    """
    Assesses the user-provided category and generated synopsis for safety.
    Returns 'safe' or 'unsafe'.
    """
    logger.info("Assessing category safety", category=category)
    prompt = prompt_manager.get_prompt("safety_checker")
    messages = prompt.invoke({"category": category, "synopsis": synopsis}).messages

    response = await llm_service.get_text_response(
        tool_name="safety_checker",
        messages=messages,
        trace_id=trace_id,
        session_id=session_id,
    )
    # Clean up the response to ensure it's one of the two literal values
    if "unsafe" in response.lower():
        return "unsafe"
    return "safe"


@tool
async def analyze_tool_error(
    error_message: str, state: Dict, trace_id: Optional[str] = None, session_id: Optional[str] = None
) -> str:
    """
    Analyzes a tool execution error and suggests a corrective action.
    This is a key part of the agent's self-correction mechanism.
    """
    logger.warning("Analyzing tool error", error_message=error_message)
    # This prompt needs to be defined in prompts.py
    prompt = prompt_manager.get_prompt("error_analyzer")
    messages = prompt.invoke({"error_message": error_message, "state": state}).messages

    return await llm_service.get_text_response(
        tool_name="error_analyzer",
        messages=messages,
        trace_id=trace_id,
        session_id=session_id,
    )


@tool
async def explain_failure_to_user(
    error_summary: str, trace_id: Optional[str] = None, session_id: Optional[str] = None
) -> str:
    """
    Generates a user-friendly message explaining why the quiz generation failed.
    """
    logger.error("Generating user-facing failure explanation", error_summary=error_summary)
    # This prompt needs to be defined in prompts.py
    prompt = prompt_manager.get_prompt("failure_explainer")
    messages = prompt.invoke({"error_summary": error_summary}).messages

    return await llm_service.get_text_response(
        tool_name="failure_explainer",
        messages=messages,
        trace_id=trace_id,
        session_id=session_id,
    )
