"""
Agent Tools: Image Generation

This module contains all tools related to generating images for the quiz.
"""
import uuid
from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field


# --- Pydantic Models for Structured Inputs ---

class GenerateImageInput(BaseModel):
    """Input for all image generation tools."""
    prompt: str = Field(..., description="A detailed text description of the desired image.")
    style: Literal["clipart", "portrait"] = Field(..., description="The artistic style of the image.")
    character_id: uuid.UUID | None = Field(None, description="The character ID to associate with a portrait.")


# --- Tool Definitions ---

@tool
async def generate_image(input_data: GenerateImageInput) -> str:
    """
    Generates an image based on a prompt and a specified style.
    Routes to the appropriate service for either fast 'clipart' or high-quality 'portrait'.
    """
    # This tool would call the appropriate image generation service (e.g., Fal.ai for clipart,
    # DALL-E for portraits) and return a URL.
    # For now, it's a placeholder.
    print(f"Generating a {input_data.style} image for prompt: {input_data.prompt}")
    return "https://placehold.co/600x400/EEE/31343C?text=Generated+Image"
