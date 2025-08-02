"""
Agent Tools: Planning & Strategy

This module contains the tools the agent uses for high-level reasoning,
planning, safety checks, and self-correction.
"""
import uuid
from typing import Dict, List, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.services.llm_service import llm_service


# --- Pydantic Models for Structured Inputs ---

class AnalyzeErrorInput(BaseModel):
    """Input for the analyze_tool_error tool."""
    failed_tool_call: Dict = Field(..., description="The full dictionary of the tool call that failed.")
    error_message: str = Field(..., description="The error message that was returned.")

class AssessSafetyInput(BaseModel):
    """Input for the assess_category_safety tool."""
    category: str = Field(..., description="The original user-provided category.")
    synopsis: str = Field(..., description="The detailed synopsis generated for the category.")

class GenerateCharacterListInput(BaseModel):
    """Input for the generate_character_list tool."""
    category: str = Field(..., description="The quiz category.")
    synopsis: str = Field(..., description="The detailed synopsis of the category.")
    research_notes: str = Field(..., description="Research notes from web and Wikipedia searches.")

class SelectCharactersInput(BaseModel):
    """Input for the select_characters_for_reuse tool."""
    candidate_list: List[str] = Field(..., description="The list of ideal character names for the quiz.")
    retrieved_characters: List[Dict] = Field(..., description="A list of existing characters retrieved from similar past quizzes.")

class ExplainFailureInput(BaseModel):
    """Input for the explain_failure_to_user tool."""
    error_code: str = Field(..., description="The internal error code (e.g., 'AI_PLANNING_FAILED').")
    error_message: str = Field(..., description="The technical error message from the system.")


# --- Tool Definitions ---

@tool
async def analyze_tool_error(input_data: AnalyzeErrorInput) -> str:
    """
    Analyzes a failed tool call and its error message to suggest a correction.
    Use this for self-correction when a tool fails unexpectedly.
    """
    # This tool would call the LLM to get a suggested fix.
    # For now, it's a placeholder.
    return "Self-correction logic would be implemented here."

@tool
async def assess_category_safety(input_data: AssessSafetyInput) -> bool:
    """
    Assesses if a category and its synopsis comply with the safety policy.
    Returns True if safe, False if unsafe.
    """
    # This tool would call an LLM with a specific safety-check prompt.
    # For now, it's a placeholder that assumes safety.
    return True

@tool
async def generate_character_list(input_data: GenerateCharacterListInput) -> List[str]:
    """
    Generates a list of potential character names based on the category, synopsis, and research.
    """
    # This tool would call the LLM to brainstorm a list of characters.
    # For now, it's a placeholder.
    return ["Character A", "Character B", "Character C", "Character D"]

@tool
async def select_characters_for_reuse(input_data: SelectCharactersInput) -> List[uuid.UUID]:
    """
    Compares a list of ideal characters against a list of existing characters
    from similar quizzes and decides which ones (by UUID) to reuse.
    """
    # This tool would call an LLM to perform the matching and selection.
    # For now, it's a placeholder.
    return []

@tool
async def explain_failure_to_user(input_data: ExplainFailureInput) -> str:
    """
    Takes a technical error and generates a user-friendly, whimsical explanation for the failure.
    """
    # This tool would call the LLM with a creative writing prompt.
    # For now, it's a placeholder.
    return "Our crystal ball seems to be a bit cloudy at the moment. Please try another magical category!"
