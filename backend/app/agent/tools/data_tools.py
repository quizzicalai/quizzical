"""
Agent Tools: Data Retrieval (RAG, Web Search, etc.)
"""
from typing import Dict, List, Optional

import structlog
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_community.utilities.wikipedia import WikipediaAPIWrapper
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import Character
from app.services.llm_service import llm_service

logger = structlog.get_logger(__name__)

# --- Pydantic Models for Tool Inputs ---

class SynopsisInput(BaseModel):
    """Input schema for the contextual session search tool."""
    category_synopsis: str = Field(description="The detailed synopsis of the quiz category.")

class CharacterInput(BaseModel):
    """Input schema for the character detail fetching tool."""
    character_id: str = Field(description="The unique identifier of the character to fetch.")

# --- Tool Definitions ---

@tool
async def search_for_contextual_sessions(
    tool_input: SynopsisInput, config: RunnableConfig, trace_id: Optional[str] = None, session_id: Optional[str] = None
) -> List[Dict]:
    """
    Performs a semantic vector search to find similar past quiz sessions.
    This is the core of the agent's RAG process.
    """
    logger.info("Searching for contextual sessions", synopsis_preview=tool_input.category_synopsis[:80])
    
    # FIX: Extract the database session from the RunnableConfig.
    # This is the correct way to inject dependencies into tools.
    db_session: Optional[AsyncSession] = config["configurable"].get("db_session")
    if not db_session:
        return {"error": "Database session not available."}

    try:
        embedding_response = await llm_service.get_embedding(input=[tool_input.category_synopsis])
        # The response is a list of embeddings, we need the first one.
        query_vector = embedding_response[0]

        # This part remains pseudo-code until a vector DB is implemented.
        # async with db_session as db:
        #     ... vector search logic ...
        return []  # Returning empty for now to avoid SQL errors.

    except Exception as e:
        logger.error("Failed to search for contextual sessions", error=str(e), exc_info=True)
        return []


@tool
async def fetch_character_details(
    tool_input: CharacterInput, config: RunnableConfig, trace_id: Optional[str] = None, session_id: Optional[str] = None
) -> Optional[Dict]:
    """Fetches the full details of a specific character from the database by its ID."""
    logger.info("Fetching character details", character_id=tool_input.character_id)
    
    # FIX: Extract the database session from the RunnableConfig.
    db_session: Optional[AsyncSession] = config["configurable"].get("db_session")
    if not db_session:
        return {"error": "Database session not available."}
        
    try:
        async with db_session as db:
            character = await db.get(Character, tool_input.character_id)
            if character:
                return {
                    "id": str(character.id),
                    "name": character.name,
                    "profile_text": character.profile_text,
                    "short_description": character.short_description,
                }
            return None
    except Exception as e:
        logger.error("Failed to fetch character details", error=str(e), exc_info=True)
        return None


# --- Web Search Tool Implementations ---

# Instantiate the search clients once to be reused.
_tavily_search = TavilySearchResults(max_results=3)
_wikipedia_search = WikipediaAPIWrapper(top_k_results=2, doc_content_chars_max=2000)

# FIX: Wrapped the search clients in functions decorated with @tool.
# This properly registers them as tools the agent can see and call.
@tool
def web_search(query: str) -> str:
    """
    A powerful web search engine. Use for current events or general knowledge.
    Input should be a concise search query.
    """
    logger.info("Performing web search", query=query)
    return _tavily_search.invoke(query)

@tool
def wikipedia_search(query: str) -> str:
    """
    Searches for a term on Wikipedia. Good for factual, encyclopedic information.
    Input should be a specific term or topic to look up.
    """
    logger.info("Performing Wikipedia search", query=query)
    return _wikipedia_search.run(query)
