"""
Agent Tools Registry

This module discovers and aggregates all the tools available to the agent.
"""
from .analysis_tools import (
    analyze_tool_error,
    assess_category_safety,
    explain_failure_to_user,
)
from .content_creation_tools import (
    draft_character_profile,
    generate_baseline_questions,
    generate_category_synopsis,
    generate_next_question,
    improve_character_profile,
    write_final_user_profile,
)
from .data_tools import (
    fetch_character_details,
    search_for_contextual_sessions,
    web_search,
    wikipedia_search,
)
from .image_tools import create_image_generation_prompt, generate_image
from .planning_tools import generate_character_list, select_characters_for_reuse
from .utility_tools import persist_session_to_database

# --- Tool Registry ---
# This list is the single source of truth for all tools available to the agent.
# FIX: Confirmed that all tools, including the newly wrapped web_search and
# wikipedia_search, are correctly included.
tool_registry = [
    # Analysis & Safety Tools
    analyze_tool_error,
    assess_category_safety,
    explain_failure_to_user,
    # Planning & Strategy Tools
    generate_character_list,
    select_characters_for_reuse,
    # Content Creation Tools
    draft_character_profile,
    generate_baseline_questions,
    generate_category_synopsis,
    generate_next_question,
    improve_character_profile,
    write_final_user_profile,
    # Data & Research Tools
    fetch_character_details,
    search_for_contextual_sessions,
    web_search,
    wikipedia_search,
    # Image Generation Tools
    create_image_generation_prompt,
    generate_image,
    # Utility & Persistence Tools
    persist_session_to_database,
]

def get_tools():
    """Returns the list of all registered agent tools."""
    return tool_registry
