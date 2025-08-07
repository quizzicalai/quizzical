"""
Agent Prompts

This module centralizes all prompt engineering for the Quizzical AI agent.
It implements a resilient fetch-or-fallback strategy. Prompts are first
looked for in the application's dynamic configuration. If not found, they
fall back to the hardcoded defaults defined in this file.
"""
from typing import Dict, Tuple

import structlog
from langchain_core.prompts import ChatPromptTemplate

from app.core.config import settings

logger = structlog.get_logger(__name__)

# =============================================================================
# Default Prompt Registry
# =============================================================================

DEFAULT_PROMPTS: Dict[str, Tuple[str, str]] = {
    "default_persona": (
        "You are Quizzical, a witty and creative quiz master. Your goal is to create "
        "engaging, fun, and surprising personality quizzes. Your tone should be "
        "playful and slightly mysterious.",
        "{user_input}",
    ),
    "synopsis_writer": (
        "You are a master storyteller. Your specialty is writing short, intriguing synopses.",
        "Write a short, engaging, and whimsical synopsis for a personality quiz about '{category}'. Keep it to 2-3 sentences.",
    ),
    "initial_planner": (
        "You are a master planner and creative director for a game studio.",
        "Create an initial plan for a personality quiz about '{category}'. I need:\n"
        "1. A detailed, engaging synopsis for the quiz.\n"
        "2. A list of 4-6 distinct character archetypes that would be good outcomes.",
    ),
    "profile_writer": (
        "You are a character designer from a top animation studio.",
        "For a quiz about '{category}', draft a detailed character profile for the archetype: '{character_name}'.\n\n"
        "Provide:\n- A short, one-sentence description.\n- A detailed profile text (2-3 paragraphs).",
    ),
    "question_generator": (
        "You are a brilliant psychologist and game designer who creates thought-provoking questions.",
        "For a quiz about '{category}', create a list of 5 diverse multiple-choice questions to help determine which of these characters a user is most like:\n{character_profiles}",
    ),
    "final_profile_writer": (
        "You are an expert at writing personalized, uplifting, and insightful personality summaries.",
        "The user's personality profile matches '{winning_character_name}'. Based on their answers:\n{quiz_history}\n\n"
        "Write a fun, personalized, and flattering result for them. Start with the title 'You are The {winning_character_name}!' and then explain *why* they match that profile.",
    ),
    "safety_checker": (
        "You are a safety classification expert. Respond with only the word 'safe' or 'unsafe'.",
        "Classify the following quiz topic and synopsis:\n\nTopic: {category}\nSynopsis: {synopsis}",
    ),
    "image_prompt_enhancer": (
        "You are an expert prompt engineer for a text-to-image model like Midjourney or SDXL.",
        "Expand the following simple concept into a rich, descriptive prompt. The final prompt should be a single, comma-separated string of descriptive keywords and phrases. The desired style is '{style}'.\n\nConcept: {concept}",
    ),
}

# =============================================================================
# Prompt Manager Service
# =============================================================================

class PromptManager:
    """A service to manage and retrieve prompt templates."""

    def get_prompt(self, prompt_name: str) -> ChatPromptTemplate:
        """
        Retrieves a prompt template by name, falling back to defaults.
        """
        prompt_config = settings.llm_prompts.get(prompt_name)
        if prompt_config and prompt_config.system_prompt:
            logger.debug("Loading prompt from dynamic configuration", prompt_name=prompt_name)
            system_template = prompt_config.system_prompt
            human_template = prompt_config.user_prompt_template
        else:
            logger.debug("Falling back to default prompt", prompt_name=prompt_name)
            if prompt_name not in DEFAULT_PROMPTS:
                raise ValueError(f"Prompt '{prompt_name}' not found.")
            system_template, human_template = DEFAULT_PROMPTS[prompt_name]
        return ChatPromptTemplate.from_messages([("system", system_template), ("human", human_template)])

prompt_manager = PromptManager()