"""
Agent Tools: Planning & Strategy
"""
from typing import List, Optional

import structlog
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.agent.prompts import prompt_manager
from app.services.llm_service import llm_service

logger = structlog.get_logger(__name__)

class InitialPlan(BaseModel):
    """The initial high-level plan for generating the quiz."""
    synopsis: str = Field(description="A detailed, engaging synopsis for the quiz category.")
    character_archetypes: List[str] = Field(description="A list of 4-6 character archetypes.")

@tool
async def create_initial_plan(
    category: str, trace_id: Optional[str] = None, session_id: Optional[str] = None
) -> InitialPlan:
    """
    Creates the initial plan for the quiz, including a synopsis and character archetypes.
    This should be the very first step in the quiz generation process.
    """
    logger.info("Creating initial plan", category=category)
    prompt = prompt_manager.get_prompt("initial_planner")
    messages = prompt.invoke({"category": category}).messages
    
    plan = await llm_service.get_structured_response(
        tool_name="initial_planner",
        messages=messages,
        response_model=InitialPlan,
        trace_id=trace_id,
        session_id=session_id,
    )
    return plan

@tool
async def assess_category_safety(
    category: str, synopsis: str, trace_id: Optional[str] = None, session_id: Optional[str] = None
) -> bool:
    """
    Assesses if a category and synopsis are safe. Returns True if safe.
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
    return "safe" in response.lower()