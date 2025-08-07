"""
Agent Tools: Content Creation
"""
from typing import Dict, List, Optional

import structlog
from langchain_core.tools import tool

from app.agent.prompts import prompt_manager
from app.agent.state import CharacterProfile, FinalResult, QuizQuestion
from app.services.llm_service import llm_service

logger = structlog.get_logger(__name__)

@tool
async def draft_character_profile(
    character_name: str, category: str, trace_id: Optional[str] = None, session_id: Optional[str] = None
) -> CharacterProfile:
    """Drafts a new character profile based on the quiz's category."""
    logger.info("Drafting character profile", character_name=character_name)
    prompt = prompt_manager.get_prompt("profile_writer")
    messages = prompt.invoke({"character_name": character_name, "category": category}).messages
    
    return await llm_service.get_structured_response(
        tool_name="profile_writer",
        messages=messages,
        response_model=CharacterProfile,
        trace_id=trace_id,
        session_id=session_id,
    )

@tool
async def generate_baseline_questions(
    category: str, character_profiles: List[Dict], trace_id: Optional[str] = None, session_id: Optional[str] = None
) -> List[QuizQuestion]:
    """Generates the initial set of baseline questions for the quiz."""
    logger.info("Generating baseline questions", category=category)
    prompt = prompt_manager.get_prompt("question_generator")
    messages = prompt.invoke({"category": category, "character_profiles": character_profiles}).messages
    
    return await llm_service.get_structured_response(
        tool_name="question_generator",
        messages=messages,
        response_model=List[QuizQuestion],
        trace_id=trace_id,
        session_id=session_id,
    )

@tool
async def write_final_user_profile(
    winning_character: Dict, quiz_history: List[Dict], trace_id: Optional[str] = None, session_id: Optional[str] = None
) -> FinalResult:
    """Writes the final, personalized user profile."""
    logger.info("Writing final user profile", character=winning_character.get("name"))
    prompt = prompt_manager.get_prompt("final_profile_writer")
    messages = prompt.invoke({
        "winning_character_name": winning_character.get("name"),
        "quiz_history": quiz_history
    }).messages

    return await llm_service.get_structured_response(
        tool_name="final_profile_writer",
        messages=messages,
        response_model=FinalResult,
        trace_id=trace_id,
        session_id=session_id,
    )