"""
Agent Tools: Content Creation

This module contains all the tools the agent uses for generative tasks,
such as writing text, creating questions, and drafting profiles.
"""
import json
import uuid
from typing import Dict, List

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.services.llm_service import llm_service

# =============================================================================
# Pydantic Models for Tool Inputs (as defined in the original file)
# =============================================================================

class SynopsisInput(BaseModel):
    """Input for the generate_category_synopsis tool."""
    category: str = Field(..., description="The raw user-provided category.")

class DraftProfileInput(BaseModel):
    """Input for the draft_character_profile tool."""
    character_name: str = Field(..., description="The name of the character to create.")
    research_notes: str = Field(..., description="Compiled research from web/Wikipedia searches.")

class ImproveProfileInput(BaseModel):
    """Input for the improve_character_profile tool."""
    character_id: uuid.UUID = Field(..., description="The UUID of the character to improve.")
    existing_profile: Dict = Field(..., description="The full existing character object from the database.")
    judge_feedback: str = Field(..., description="The feedback provided by the LLM-as-Judge.")

class BaselineQuestionsInput(BaseModel):
    """Input for the generate_baseline_questions tool."""
    character_profiles: List[Dict] = Field(..., description="The list of finalized character profiles for this quiz.")
    contextual_sessions: List[Dict] = Field(..., description="A list of similar past sessions to use for context.")

class NextQuestionInput(BaseModel):
    """Input for the generate_next_question tool."""
    quiz_history: List[Dict] = Field(..., description="The full history of questions asked and answers given so far.")
    character_profiles: List[Dict] = Field(..., description="The list of finalized character profiles for this quiz.")

class FinalProfileInput(BaseModel):
    """Input for the write_final_user_profile tool."""
    winning_character: Dict = Field(..., description="The full profile of the character the user matched with.")
    quiz_history: List[Dict] = Field(..., description="The full history of the user's answers.")


# =============================================================================
# Pydantic Models for Structured LLM Outputs
# =============================================================================

class CharacterProfileOutput(BaseModel):
    """Expected output structure for a drafted character profile."""
    name: str
    short_description: str
    profile_text: str

class QuestionOption(BaseModel):
    """A single multiple-choice option for a quiz question."""
    text: str

class QuestionOutput(BaseModel):
    """Expected output structure for a single generated quiz question."""
    question_text: str
    options: List[QuestionOption]

class QuestionListOutput(BaseModel):
    """Expected output for a list of baseline questions."""
    questions: List[QuestionOutput]

class FinalProfileOutput(BaseModel):
    """Expected output for the final user profile."""
    title: str
    description: str


# =============================================================================
# Tool Definitions
# =============================================================================

@tool
async def generate_category_synopsis(input_data: SynopsisInput) -> str:
    """Generates a rich, semantic synopsis for a given quiz category."""
    synopsis = await llm_service.invoke_llm(
        tool_name="synopsis_writer",
        prompt_kwargs={"category": input_data.category},
    )
    return synopsis

@tool
async def draft_character_profile(input_data: DraftProfileInput) -> Dict:
    """Drafts a new character profile, including a short and long description."""
    profile = await llm_service.invoke_llm(
        tool_name="profile_writer",
        prompt_kwargs={
            "character_name": input_data.character_name,
            "research_notes": input_data.research_notes,
        },
        response_model=CharacterProfileOutput,
    )
    return profile.model_dump()

@tool
async def improve_character_profile(input_data: ImproveProfileInput) -> Dict:
    """Improves an existing character profile based on judge feedback."""
    improved_profile = await llm_service.invoke_llm(
        tool_name="profile_improver", # Assumes a 'profile_improver' config exists
        prompt_kwargs={
            "existing_profile": json.dumps(input_data.existing_profile),
            "judge_feedback": input_data.judge_feedback,
        },
        response_model=CharacterProfileOutput,
    )
    return improved_profile.model_dump()

@tool
async def generate_baseline_questions(input_data: BaselineQuestionsInput) -> List[Dict]:
    """Generates the initial baseline questions for the quiz."""
    question_list = await llm_service.invoke_llm(
        tool_name="question_generator",
        prompt_kwargs={
            "character_profiles": json.dumps(input_data.character_profiles),
            "contextual_sessions": json.dumps(input_data.contextual_sessions),
        },
        response_model=QuestionListOutput,
    )
    return [q.model_dump() for q in question_list.questions]

@tool
async def generate_next_question(input_data: NextQuestionInput) -> Dict:
    """Generates the next adaptive question based on the user's history."""
    next_question = await llm_service.invoke_llm(
        tool_name="adaptive_question_generator", # Assumes this config exists
        prompt_kwargs={
            "quiz_history": json.dumps(input_data.quiz_history),
            "character_profiles": json.dumps(input_data.character_profiles),
        },
        response_model=QuestionOutput,
    )
    return next_question.model_dump()

@tool
async def write_final_user_profile(input_data: FinalProfileInput) -> Dict:
    """Writes the final, personalized user profile based on their answers and winning character."""
    final_profile = await llm_service.invoke_llm(
        tool_name="final_profile_writer", # Assumes this config exists
        prompt_kwargs={
            "winning_character": json.dumps(input_data.winning_character),
            "quiz_history": json.dumps(input_data.quiz_history),
        },
        response_model=FinalProfileOutput,
    )
    return final_profile.model_dump()
