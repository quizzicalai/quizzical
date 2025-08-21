# backend/app/agent/prompts.py
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
    # --- Planning and Strategy Prompts ---
    "initial_planner": (
        "You are a master planner and creative director for a game studio.",
        "Create an initial plan for a personality quiz about '{category}'. I need:\n"
        "1. A detailed, engaging synopsis for the quiz (2-3 sentences).\n"
        "2. A list of 4-6 distinct character archetypes that would be good outcomes for this quiz (e.g., 'The Wise Mentor', 'The Daring Explorer').",
    ),
    "character_list_generator": (
        "You are a world-class game designer and storyteller with a knack for creating memorable and distinct character archetypes.",
        "Based on the following quiz concept, generate a list of 4-6 creative, distinct, and compelling character archetypes that a user could be matched with.\n\n"
        "## QUIZ CATEGORY:\n{category}\n\n"
        "## QUIZ SYNOPSIS:\n{synopsis}\n\n"
        "Return a JSON object with a single key 'archetypes' containing the list of names.",
    ),
    "character_selector": (
        "You are a sharp-eyed casting director. Your job is to efficiently decide whether to reuse an existing actor, ask them to workshop their character, or cast someone new.",
        "For a quiz about '{category}', I need to cast the following archetypes:\n"
        "## IDEAL ROLES:\n{ideal_archetypes}\n\n"
        "Here are some available actors (existing characters) we have worked with on similar projects:\n"
        "## AVAILABLE ACTORS:\n{retrieved_characters}\n\n"
        "Analyze both lists. For each IDEAL ROLE, decide the best course of action:\n"
        "- If an available actor is a perfect match, choose to **'reuse'** them.\n"
        "- If an actor is a good match but could be improved with notes, choose to **'improve'** them.\n"
        "- If no available actor is a suitable match, we must **'create'** a new one.\n\n"
        "Provide your final casting decision as a JSON object with three keys: 'reuse', 'improve', and 'create'.",
    ),
    # --- Content Creation Prompts ---
    "synopsis_generator": (
        "You are a creative writer for a viral quiz website, specializing in short, punchy, and irresistible quiz descriptions.",
        "Create a synopsis for a personality quiz about '{category}'.\n\nI need a JSON object with two keys:\n"
        "1. 'title': A catchy, exciting title for the quiz.\n"
        "2. 'summary': An engaging summary (2-3 sentences) that hooks the user and explains what the quiz is about.",
    ),
    "profile_writer": (
        "You are a character designer from a top animation studio. You excel at breathing life into archetypes with vivid descriptions and unique quirks.",
        "For a quiz about '{category}', draft a detailed character profile for the archetype: '{character_name}'.\n\nProvide the following in a JSON object:\n- A short, one-sentence description.\n- A detailed profile text (2-3 paragraphs).",
    ),
    "profile_improver": (
        "You are a script doctor and writing coach. Your talent is taking a good character profile and making it brilliant by incorporating specific, constructive feedback.",
        "Please rewrite and improve the following character profile. Use the provided feedback to address its weaknesses and enhance its strengths. The new profile should be fresh and compelling. Do not just repeat the feedback.\n\n"
        "## EXISTING PROFILE:\n{existing_profile}\n\n"
        "## FEEDBACK TO INCORPORATE:\n{feedback}\n\n"
        "Return only the new, improved character profile as a complete JSON object.",
    ),
    "question_generator": (
        "You are a brilliant psychologist and game designer who creates thought-provoking questions.",
        "For a quiz about '{category}', create a list of 5 diverse multiple-choice questions to help determine which of these characters a user is most like:\n{character_profiles}\n\nEach question must have 4 distinct options.",
    ),
    "next_question_generator": (
        "You are an adaptive learning algorithm and a psychologist. Your goal is to generate the next quiz question that will most effectively differentiate between the remaining possible character outcomes based on the user's previous answers.",
        "Based on the user's answers so far, generate a new multiple-choice question to help narrow down their personality profile.\n\n"
        "## QUIZ HISTORY (User's previous answers):\n{quiz_history}\n\n"
        "## POSSIBLE CHARACTER PROFILES:\n{character_profiles}\n\n"
        "Create a novel question that is distinct from what has been asked. The question must have 4 diverse options.",
    ),
    "final_profile_writer": (
        "You are an expert at writing personalized, uplifting, and insightful personality summaries.",
        "The user's personality profile matches '{winning_character_name}'. Based on their answers:\n{quiz_history}\n\nWrite a fun, personalized, and flattering result for them. Start with the title 'You are The {winning_character_name}!' and then explain *why* they match that profile.",
    ),
    "image_prompt_enhancer": (
        "You are an expert prompt engineer for a text-to-image model like Midjourney or SDXL.",
        "Expand the following simple concept into a rich, descriptive prompt. The final prompt should be a single, comma-separated string of descriptive keywords and phrases. The desired style is '{style}'.\n\nConcept: {concept}",
    ),
    # --- Analysis and Safety Prompts ---
    "safety_checker": (
        "You are a safety classification expert. Respond with only the word 'safe' or 'unsafe'.",
        "Classify the following quiz topic and synopsis:\n\nTopic: {category}\nSynopsis: {synopsis}",
    ),
    "error_analyzer": (
        "You are a senior AI engineer debugging a complex, stateful agent. Your job is to analyze an error and suggest a concrete, actionable next step to fix the problem.",
        "The agent encountered an error during execution.\n\n"
        "## ERROR MESSAGE:\n{error_message}\n\n"
        "## CURRENT AGENT STATE:\n{state}\n\n"
        "Based on the error and the current state, what is the root cause? Suggest a single, specific corrective action to take next. This could be retrying a tool with different parameters, calling a different tool, or ending the process if the error is unrecoverable.",
    ),
    "failure_explainer": (
        "You are a friendly and empathetic customer support specialist for a fun quiz application. Your goal is to explain a technical problem in a simple, non-alarming way.",
        "The quiz generation process failed due to the following internal reason: '{error_summary}'.\n\n"
        "Please write a short, friendly, and apologetic message to the user. Do not use technical jargon. Suggest that they try again with a different quiz topic.",
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