"""
Agent Tools: Image Generation

This module contains all tools related to generating images for the quiz.
It uses fal.ai for fast, on-demand image creation.

NOTE: This tool requires the `fal-ai` package to be installed (`poetry add fal-ai`)
and the `FAL_AI_KEY` secret to be configured in your environment and settings.
"""
import uuid
from typing import Literal

import fal
import structlog
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.core.config import settings

logger = structlog.get_logger(__name__)

# --- Pydantic Models for Structured Inputs ---


class GenerateImageInput(BaseModel):
    """Input for all image generation tools."""

    prompt: str = Field(
        ..., description="A detailed text description of the desired image."
    )
    style: Literal["clipart", "portrait"] = Field(
        ..., description="The artistic style of the image."
    )
    character_id: uuid.UUID | None = Field(
        None, description="The character ID to associate with a portrait."
    )


# --- Tool Definitions ---


@tool
async def generate_image(input_data: GenerateImageInput) -> str:
    """
    Generates an image based on a prompt and a specified style using the fal.ai API.
    This tool is optimized for speed to ensure a good user experience.
    """
    # Configure fal.ai with credentials from settings
    # NOTE: You must add `FAL_AI_KEY: SecretStr` to your `Settings` model in `config.py`
    # and the corresponding secret to your configuration source (e.g., .env file).
    try:
        fal.config.credentials = settings.FAL_AI_KEY.get_secret_value()
    except AttributeError:
        logger.error(
            "FAL_AI_KEY not found in settings. Image generation will fail."
        )
        return "https://placehold.co/600x400/EEE/31343C?text=Image+Generation+Error"

    # Enhance the prompt with the desired style for better results
    enhanced_prompt = f"A vibrant {input_data.style} of: {input_data.prompt}. Clean background, simple, colorful."

    logger.info("Generating image with fal.ai", prompt=enhanced_prompt)

    try:
        # Use a fast model from fal.ai suitable for quick generation
        # The 'fal-ai/fast-sdxl' model is a good choice for speed.
        result = await fal.run(
            "fal-ai/fast-sdxl",
            arguments={
                "prompt": enhanced_prompt,
                "negative_prompt": "blurry, ugly, deformed, noisy, text, watermark",
            },
        )
        image_url = result["images"][0]["url"]
        logger.info("Successfully generated image", image_url=image_url)
        return image_url

    except Exception as e:
        logger.error(
            "Image generation with fal.ai failed",
            prompt=enhanced_prompt,
            error=str(e),
        )
        # Provide a placeholder as a fallback to prevent the quiz from breaking
        return "https://placehold.co/600x400/EEE/31343C?text=Image+Generation+Failed"
