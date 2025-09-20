# backend/app/agent/tools/planning_tools.py
"""
Agent Tools: Planning & Strategy

Tools here are thin wrappers around prompt templates + LLM service.
They are used by the agent planner and by the bootstrap steps in graph.py.
"""

from __future__ import annotations

from typing import Dict, List, Optional

import structlog
from langchain_core.tools import tool
from pydantic import BaseModel, Field, ValidationError

from app.agent.prompts import prompt_manager
from app.services.llm_service import llm_service

logger = structlog.get_logger(__name__)

# -------------------------
# Structured outputs
# -------------------------

class InitialPlan(BaseModel):
    """Output of the initial planning stage."""
    synopsis: str = Field(description="Engaging synopsis (2–3 sentences) for the quiz category.")
    ideal_archetypes: List[str] = Field(description="4–6 ideal character archetypes.")

class CharacterCastingDecision(BaseModel):
    """Decisions whether to reuse, improve, or create characters."""
    reuse: List[Dict] = Field(default_factory=list, description="Existing characters to reuse as-is.")
    improve: List[Dict] = Field(default_factory=list, description="Existing characters to improve.")
    create: List[str] = Field(default_factory=list, description="New archetypes to create from scratch.")

# -------------------------
# Tools
# -------------------------

@tool
async def generate_character_list(
    category: str,
    synopsis: str,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> List[str]:
    """
    Generates a list of 4–6 creative character archetypes for the quiz.
    """
    logger.info("tool.generate_character_list.start", category=category)
    prompt = prompt_manager.get_prompt("character_list_generator")
    messages = prompt.invoke({"category": category, "synopsis": synopsis}).messages

    class _ArchetypeList(BaseModel):
        archetypes: List[str]

    try:
        resp = await llm_service.get_structured_response(
            "character_list_generator", messages, _ArchetypeList, trace_id, session_id
        )
        logger.info("tool.generate_character_list.ok", count=len(resp.archetypes))
        return resp.archetypes
    except ValidationError as e:
        logger.error("tool.generate_character_list.validation", error=str(e), exc_info=True)
        return []
    except Exception as e:
        logger.error("tool.generate_character_list.fail", error=str(e), exc_info=True)
        return []


@tool
async def select_characters_for_reuse(
    category: str,
    ideal_archetypes: List[str],
    retrieved_characters: List[Dict],
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> CharacterCastingDecision:
    """
    Decision engine: for each ideal archetype, decide to reuse / improve / create.
    """
    logger.info(
        "tool.select_characters_for_reuse.start",
        category=category,
        ideal_count=len(ideal_archetypes),
        retrieved_count=len(retrieved_characters),
    )
    prompt = prompt_manager.get_prompt("character_selector")
    messages = prompt.invoke({
        "category": category,
        "ideal_archetypes": ideal_archetypes,
        "retrieved_characters": retrieved_characters,
    }).messages
    try:
        out = await llm_service.get_structured_response(
            "character_selector", messages, CharacterCastingDecision, trace_id, session_id
        )
        logger.info(
            "tool.select_characters_for_reuse.ok",
            reuse=len(out.reuse), improve=len(out.improve), create=len(out.create)
        )
        return out
    except Exception as e:
        logger.error("tool.select_characters_for_reuse.fail", error=str(e), exc_info=True)
        return CharacterCastingDecision()
