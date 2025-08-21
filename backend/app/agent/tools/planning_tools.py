# backend/app/agent/tools/planning_tools.py
"""
Agent Tools: Planning & Strategy
"""
from typing import Dict, List, Optional

import structlog
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.agent.prompts import prompt_manager
from app.services.llm_service import llm_service

logger = structlog.get_logger(__name__)

# --- Structured Inputs and Outputs for Planning Tools ---

class InitialPlan(BaseModel):
    """The structured output of the initial planning stage."""
    synopsis: str = Field(description="An engaging synopsis for the quiz category.")
    ideal_archetypes: List[str] = Field(description="A list of 4-6 ideal character archetypes.")

class CharacterCastingDecision(BaseModel):
    """The structured output of the character selection tool."""
    reuse: List[Dict] = Field(description="List of existing characters to reuse as-is.")
    improve: List[Dict] = Field(description="List of existing characters to improve.")
    create: List[str] = Field(description="List of new character archetypes to create from scratch.")


# --- Strategic Tool Definitions ---

@tool
async def generate_character_list(
    category: str, synopsis: str, trace_id: Optional[str] = None, session_id: Optional[str] = None
) -> List[str]:
    """Generates a list of 4-6 creative and distinct character archetypes for the quiz."""
    logger.info("Generating character archetype list", category=category)
    # This can reuse part of the initial_planner prompt or have its own.
    prompt = prompt_manager.get_prompt("character_list_generator")
    messages = prompt.invoke({"category": category, "synopsis": synopsis}).messages
    
    class ArchetypeList(BaseModel):
        archetypes: List[str]

    response = await llm_service.get_structured_response(
        "character_list_generator", messages, ArchetypeList, trace_id, session_id
    )
    return response.archetypes


@tool
async def select_characters_for_reuse(
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
