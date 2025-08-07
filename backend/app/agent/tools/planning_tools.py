"""
Agent Tools: Planning & Strategy
"""
from typing import Dict, List, Optional

import structlog
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.agent.prompts import prompt_manager
from app.agent.state import CharacterProfile
from app.services.llm_service import llm_service

logger = structlog.get_logger(__name__)

# --- Structured Inputs and Outputs for Planning Tools ---

class InitialPlan(BaseModel):
    synopsis: str = Field(description="An engaging synopsis for the quiz category.")
    ideal_archetypes: List[str] = Field(description="A list of 4-6 ideal character archetypes.")

class CharacterCastingDecision(BaseModel):
    """The structured output of the character selection tool."""
    reuse: List[Dict] = Field(description="List of existing characters to reuse as-is.")
    improve: List[Dict] = Field(description="List of existing characters to improve.")
    create: List[str] = Field(description="List of new character archetypes to create from scratch.")


# --- Strategic Tool Definitions ---

@tool
async def create_initial_plan(
    category: str, trace_id: Optional[str] = None, session_id: Optional[str] = None
) -> InitialPlan:
    """Creates the initial plan, defining the synopsis and ideal characters."""
    logger.info("Creating initial plan", category=category)
    prompt = prompt_manager.get_prompt("initial_planner")
    messages = prompt.invoke({"category": category}).messages
    return await llm_service.get_structured_response(
        "initial_planner", messages, InitialPlan, trace_id, session_id
    )

@tool
async def select_characters_for_reuse_or_improvement(
    category: str, ideal_archetypes: List[str], retrieved_characters: List[Dict],
    trace_id: Optional[str] = None, session_id: Optional[str] = None
) -> CharacterCastingDecision:
    """The Decision Engine. Compares ideal archetypes to retrieved characters and decides the strategy."""
    logger.info("Casting characters: deciding to reuse, improve, or create.")
    prompt = prompt_manager.get_prompt("character_selector")
    messages = prompt.invoke({
        "category": category,
        "ideal_archetypes": ideal_archetypes,
        "retrieved_characters": retrieved_characters,
    }).messages
    return await llm_service.get_structured_response(
        "character_selector", messages, CharacterCastingDecision, trace_id, session_id
    )

@tool
async def improve_character_profile(
    existing_profile: Dict, feedback: str, trace_id: Optional[str] = None, session_id: Optional[str] = None
) -> CharacterProfile:
    """Improves an existing character profile using past feedback."""
    logger.info("Improving existing character profile", name=existing_profile.get("name"))
    prompt = prompt_manager.get_prompt("profile_improver")
    messages = prompt.invoke({
        "existing_profile": existing_profile,
        "feedback": feedback,
    }).messages
    return await llm_service.get_structured_response(
        "profile_improver", messages, CharacterProfile, trace_id, session_id
    )