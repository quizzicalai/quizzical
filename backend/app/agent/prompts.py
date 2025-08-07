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
# This is the single source of truth for all agent instructions.
# =============================================================================

DEFAULT_PROMPTS: Dict[str, Tuple[str, str]] = {
    "initial_planner": (
        "You are a master planner and creative director for a game studio.",
        "Create an initial plan for a personality quiz about '{category}'. I need:\n"
        "1. A detailed, engaging synopsis for the quiz (2-3 sentences).\n"
        "2. A list of 4-6 distinct character archetypes that would be good outcomes for this quiz (e.g., 'The Wise Mentor', 'The Daring Explorer').",
    ),
    "character_selector": (
        "You are a sharp-eyed casting director for a film. Your job is to efficiently decide whether to reuse an existing actor, ask them to workshop their character based on notes, or cast someone entirely new.",
        "For a quiz about '{category}', I need to cast the following archetypes:\n"
        "## IDEAL ROLES:\n{ideal_archetypes}\n\n"
        "Here are some actors (existing characters) we have worked with on similar projects. They have notes on their performance (quality_score) and how long it's been since they worked (last_updated).\n"
        "## AVAILABLE ACTORS:\n{retrieved_characters}\n\n"
        "Analyze both lists. For each IDEAL ROLE, decide the best course of action:\n"
        "- If an available actor is a perfect match, recently updated (e.g., within the last 3 months), and has a good score (e.g., 7 or higher), choose to **'reuse'** them.\n"
        "- If an actor is a good match but is old or has a low score and specific feedback, choose to **'improve'** them.\n"
        "- If no available actor is a suitable match for an ideal role, we must **'create'** a new one.\n\n"
        "Provide your final casting decision as a JSON object with three keys: 'reuse', 'improve', and 'create'.",
    ),
    "profile_improver": (
        "You are a script doctor and writing coach. Your talent is taking a good character profile and making it brilliant by incorporating specific, constructive feedback.",
        "Please rewrite and improve the following character profile. Use the provided feedback to address its weaknesses and enhance its strengths. The new profile should be fresh and compelling. Do not just repeat the feedback.\n\n"
        "## EXISTING PROFILE:\n{existing_profile}\n\n"
        "## FEEDBACK TO INCORPORATE:\n{feedback}\n\n"
        "Return only the new, improved character profile as a complete JSON object.",
    ),
    "profile_writer": (
        "You are a character designer from a top animation studio. You excel at breathing life into archetypes with vivid descriptions and unique quirks.",
        "For a quiz about '{category}', draft a detailed character profile for the archetype: '{character_name}'.\n\nProvide the following:\n- A short, one-sentence description.\n- A detailed profile text (2-3 paragraphs).",
    ),
    "question_generator": (
        "You are a brilliant psychologist and game designer who creates thought-provoking questions.",
        "For a quiz about '{category}', create a list of 5 diverse multiple-choice questions to help determine which of these characters a user is most like:\n{character_profiles}",
    ),
    "final_profile_writer": (
        "You are an expert at writing personalized, uplifting, and insightful personality summaries.",
        "The user's personality profile matches '{winning_character_name}'. Based on their answers:\n{quiz_history}\n\nWrite a fun, personalized, and flattering result for them. Start with the title 'You are The {winning_character_name}!' and then explain *why* they match that profile.",
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
# Prompt Manager Service (No changes needed to this class)
# =============================================================================

class PromptManager:
    """A service to manage and retrieve prompt templates."""

    def get_prompt(self, prompt_name: str) -> ChatPromptTemplate:
        """
        Retrieves a prompt template by name.

        It first checks the dynamic app settings. If not found,
        it falls back to the local DEFAULT_PROMPTS registry.
        """
        prompt_config = settings.llm_prompts.get(prompt_name)

        if prompt_config and prompt_config.system_prompt and prompt_config.user_prompt_template:
            logger.debug("Loading prompt from dynamic configuration", prompt_name=prompt_name)
            system_template = prompt_config.system_prompt
            human_template = prompt_config.user_prompt_template
        else:
            logger.debug("Prompt not in config, falling back to default", prompt_name=prompt_name)
            if prompt_name not in DEFAULT_PROMPTS:
                raise ValueError(f"Prompt '{prompt_name}' not found in dynamic config or defaults.")
            system_template, human_template = DEFAULT_PROMPTS[prompt_name]

        return ChatPromptTemplate.from_messages(
            [
                ("system", system_template),
                ("human", human_template),
            ]
        )

# Create a single instance of the manager for the application to use
prompt_manager = PromptManager()