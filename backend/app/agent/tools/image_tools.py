"""
Agent Tools: Image Generation
"""
from typing import Literal, Optional

import fal_client as fal
import structlog
from langchain_core.tools import tool

from app.agent.prompts import prompt_manager
from app.core.config import settings
from app.services.llm_service import llm_service

logger = structlog.get_logger(__name__)

@tool
async def create_image_generation_prompt(
    concept: str, style: Literal["clipart", "portrait"], trace_id: Optional[str] = None, session_id: Optional[str] = None
) -> str:
    """
    Takes a simple concept and expands it into a rich, detailed prompt suitable
    for a text-to-image model like SDXL.
    """
    logger.info("Enhancing image prompt", concept=concept, style=style)
    prompt_template = prompt_manager.get_prompt("image_prompt_enhancer")
    messages = prompt_template.invoke({"concept": concept, "style": style}).messages

    return await llm_service.get_text_response(
        tool_name="image_prompt_enhancer",
        messages=messages,
        trace_id=trace_id,
        session_id=session_id,
    )

@tool
async def generate_image(prompt: str) -> str:
    """
    Generates an image from a detailed prompt using the fal.ai API.
    This tool is optimized for speed. It expects a rich prompt created by the
    `create_image_generation_prompt` tool.
    """
    # 1. Configuration Check: Ensure the API key is present before making any calls.
    api_key = settings.FAL_AI_KEY.get_secret_value() if settings.FAL_AI_KEY else None
    if not api_key:
        logger.error(
            "FAL_AI_KEY is not configured in settings or .env file. "
            "Image generation is disabled."
        )
        return "https://placehold.co/600x400/EEE/31343C?text=Image+Config+Error"

    logger.info("Generating image with fal.ai", prompt_summary=prompt[:80])
    
    # 2. API Call: Proceed with the image generation.
    try:
        # The fal_client library automatically handles credentials if the FAL_TOKEN env var is set,
        # but we can also pass it directly for more robust control.
        result = await fal.run(
            "fal-ai/fast-sdxl",
            arguments={"prompt": prompt, "negative_prompt": "blurry, text, watermark"},
            # Note: The fal_client library can also pick up FAL_TOKEN from the environment.
            # Explicitly setting credentials here is an option if needed, but often not required.
            # credentials=api_key 
        )
        image_url = result["images"][0]["url"]
        logger.info("Successfully generated image", image_url=image_url)
        return image_url
    except Exception as e:
        logger.error("Image generation with fal.ai failed", error=str(e), exc_info=True)
        return "https://placehold.co/600x400/EEE/31343C?text=Image+Gen+Failed"