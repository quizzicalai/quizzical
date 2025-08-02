"""
Agent Tools: Content Creation

This module contains all the tools the agent uses for generative tasks,
such as writing text, creating questions, and drafting profiles.
"""
import uuid
from typing import Dict, List

from langchain_core.tools import tool
from pydantic import BaseModel, Field


# --- Pydantic Models for Structured Inputs ---

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


# --- Tool Definitions ---

@tool
async def generate_category_synopsis(input_data: SynopsisInput) -> str:
    """Generates a rich, semantic synopsis for a given quiz category."""
    return f"This is a detailed synopsis for the category: {input_data.category}."

@tool
async def draft_character_profile(input_data: DraftProfileInput) -> Dict:
    """Drafts a new character profile, including a short and long description."""
    return {
        "name": input_data.character_name,
        "short_description": f"A short description for {input_data.character_name}.",
        "profile_text": f"A long, detailed profile for {input_data.character_name} based on research.",
    }

@tool
async def improve_character_profile(input_data: ImproveProfileInput) -> Dict:
    """Improves an existing character profile based on judge feedback."""
    return {
        "profile_text": f"This is an improved profile for character ID {input_data.character_id}."
    }

@tool
async def generate_baseline_questions(input_data: BaselineQuestionsInput) -> List[Dict]:
    """Generates the initial 10 baseline questions for the quiz."""
    return [{"question_text": "What is your favorite color?", "options": ["Red", "Blue"]}]

@tool
async def generate_next_question(input_data: NextQuestionInput) -> Dict:
    """Generates the next adaptive question based on the user's history."""
    return {"question_text": "What is your quest?", "options": ["To seek the Holy Grail", "I don't know"]}

@tool
async def write_final_user_profile(input_data: FinalProfileInput) -> Dict:
    """Writes the final, personalized user profile based on their answers and winning character."""
    return {
        "title": f"You are {input_data.winning_character['name']}!",
        "description": "A personalized description based on your journey."
    }
