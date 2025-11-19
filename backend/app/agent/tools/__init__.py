# backend/app/agent/tools/__init__.py
"""
Agent Tools Registry

This module discovers and aggregates all tools available to the agent.
It is the single source of truth for ToolNode registration.

Notes:
- Keep imports narrowly-scoped to avoid circulars.
- Tools themselves contain their own error handling; this registry only aggregates.
"""

from __future__ import annotations

import structlog
from langchain_core.tools import BaseTool

from .content_creation_tools import (
    draft_character_profile,
    generate_baseline_questions,
    draft_character_profiles,
    generate_next_question,
    write_final_user_profile,
)
from .data_tools import (
    web_search,
    wikipedia_search,
)
from .planning_tools import (
    plan_quiz,
    generate_character_list,
)

logger = structlog.get_logger(__name__)

# -----------------------------------------------------------------------------
# Tool Registry (authoritative list; order matters for some planners)
# -----------------------------------------------------------------------------
tool_registry: list[BaseTool] = [
    # --- Planning & Strategy ---
    plan_quiz,                  # New: wrapper over initial plan (synopsis + archetypes)
    generate_character_list,

    # --- Content Creation ---
    draft_character_profile,
    generate_baseline_questions,
    generate_next_question,
    write_final_user_profile,
    draft_character_profiles,   # bulk profile writer (uses draft_character_profile)

    # --- Data / Research ---
    web_search,
    wikipedia_search,
]

# Sanity check: warn on duplicate tool names (prevents planner ambiguity)
try:
    names = [t.name for t in tool_registry]  # type: ignore[attr-defined]
    dups = {n for n in names if names.count(n) > 1}
    if dups:
        logger.warning("Duplicate tool names detected in registry", duplicates=sorted(list(dups)))
    logger.info("Agent tools registered", count=len(tool_registry), tools=sorted(set(names)))
except Exception as _e:
    # Never fail import due to logging issues
    logger.debug("Tool registry logging skipped", error=str(_e))

def get_tools() -> list[BaseTool]:
    """
    Returns the list of all registered agent tools.

    Returned list is the live registry (ToolNode reads directly from it).
    Callers must treat it as read-only.
    """
    return tool_registry
