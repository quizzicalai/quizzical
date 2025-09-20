# backend/app/agent/tools/image_tools.py
"""
Agent Tools: Image Prompting & Generation

- Prompt enhancer (text-only; uses LLM)
- Image generation via fal.ai (optional)
  * Secrets are read from ENV (FAL_KEY or FAL_TOKEN). We also attempt a
    best-effort read from settings if such a field exists, but we do not
    require non-secret config for this to function.
"""

from __future__ import annotations

import os
from typing import Literal, Optional

import structlog
from langchain_core.tools import tool

try:
    import fal_client as fal
except Exception:  # pragma: no cover
    fal = None  # type: ignore

from app.agent.prompts import prompt_manager
from app.services.llm_service import llm_service

logger = structlog.get_logger(__name__)


@tool
async def create_image_generation_prompt(
    concept: str,
    style: Literal["clipart", "portrait"],
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> str:
    """
    Expands a simple concept into a rich, detailed prompt for text-to-image models.
    """
    logger.info("tool.create_image_generation_prompt.start", concept=concept, style=style)
    prompt_template = prompt_manager.get_prompt("image_prompt_enhancer")
    messages = prompt_template.invoke({"concept": concept, "style": style}).messages

    try:
        text = await llm_service.get_text_response(
            tool_name="image_prompt_enhancer",
            messages=messages,
            trace_id=trace_id,
            session_id=session_id,
        )
        logger.info("tool.create_image_generation_prompt.ok", length=len(text or ""))
        return text
    except Exception as e:
        logger.error("tool.create_image_generation_prompt.fail", error=str(e), exc_info=True)
        return f"{concept}, {style}, high quality, clean background"


@tool
async def generate_image(prompt: str) -> str:
    """
    Generates an image using fal.ai (fast SDXL variant).
    Returns a placeholder URL on configuration/runtime errors.
    """
    if fal is None:
        logger.error("tool.generate_image.unavailable", reason="fal_client not installed")
        return "https://placehold.co/600x400/EEE/31343C?text=Image+Unavailable"

    # Secrets: prefer ENV (FAL_KEY or FAL_TOKEN). We do NOT read from YAML.
    fal_token = os.getenv("FAL_KEY") or os.getenv("FAL_TOKEN")
    if not fal_token:
        # try a best-effort read if your Settings accidentally exposes it (won't raise if missing)
        try:
            from app.core.config import settings as _settings  # lazy import
            fal_token = getattr(_settings, "FAL_AI_KEY", None)
            if hasattr(fal_token, "get_secret_value"):
                fal_token = fal_token.get_secret_value()  # type: ignore
        except Exception:
            fal_token = None

    if not fal_token:
        logger.error("tool.generate_image.nokey", hint="Set FAL_KEY or FAL_TOKEN in environment.")
        return "https://placehold.co/600x400/EEE/31343C?text=Image+Config+Error"

    logger.info("tool.generate_image.start", prompt_preview=prompt[:80])
    try:
        # fal_client reads FAL_KEY/FAL_TOKEN from environment; ensure it's set for the subprocess
        os.environ.setdefault("FAL_KEY", fal_token)
        result = await fal.run(
            "fal-ai/fast-sdxl",
            arguments={"prompt": prompt, "negative_prompt": "blurry, text, watermark"},
        )
        image_url = result["images"][0]["url"]
        logger.info("tool.generate_image.ok", image_url=image_url)
        return image_url
    except Exception as e:
        logger.error("tool.generate_image.fail", error=str(e), exc_info=True)
        return "https://placehold.co/600x400/EEE/31343C?text=Image+Gen+Failed"
