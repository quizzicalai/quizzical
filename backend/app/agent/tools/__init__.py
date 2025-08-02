"""
Agent Tools Registry

This module discovers and aggregates all the tools available to the agent.
By centralizing the tool registration, we create a single, flexible entry point
that the agent graph can use.

To add a new tool to the agent, simply create it in this directory and add it
to the `tool_registry` list. This is the key to the flexible architecture that
will allow for future expansion (e.g., connecting to an MCP server).
"""

from .planning_tools import (
    analyze_tool_error,
    assess_category_safety,
    explain_failure_to_user,
    generate_character_list,
    select_characters_for_reuse,
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
from .image_tools import generate_image
from .utility_tools import persist_session_to_database

# --- Tool Registry ---

# This list is the single source of truth for all tools available to the agent.
# The LangGraph ToolExecutor will be initialized with this list.
tool_registry = [
    # Planning & Strategy Tools
    analyze_tool_error,
    assess_category_safety,
    explain_failure_to_user,
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
    generate_image,
    # Utility & Persistence Tools
    persist_session_to_database,
]
