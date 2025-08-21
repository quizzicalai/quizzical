# backend/app/agent/tools/data_tools.py
"""
Agent Tools: Data Retrieval (RAG, Web Search, etc.)
"""
from typing import Dict, List, Optional

import structlog
from langchain_core.tools import tool
from langchain_community.tools.tavily_search import TavilySearchResults
from langchain_community.utilities.wikipedia import WikipediaAPIWrapper

from app.api.dependencies import async_session_factory
from app.models.db import Character, SessionHistory
from app.services.llm_service import llm_service

logger = structlog.get_logger(__name__)


@tool
async def search_for_contextual_sessions(
    category_synopsis: str, trace_id: Optional[str] = None, session_id: Optional[str] = None
) -> List[Dict]:
    """
    Performs a semantic vector search to find similar past quiz sessions.
    This is the core of the agent's RAG process.
    """
    logger.info("Searching for contextual sessions", synopsis_preview=category_synopsis[:80])
    try:
        embedding_response = await llm_service.get_embedding(input=[category_synopsis])
        query_vector = embedding_response[0]

        # FIX: Use the async session factory correctly.
        async with async_session_factory() as db:
            similar_sessions = await db.execute(
                "SELECT * FROM session_history ORDER BY synopsis_embedding <-> :vector LIMIT 5",
                {"vector": str(query_vector)}
            )
            results = []
            for session in similar_sessions.fetchall():
                # This part would need to be fleshed out to join with characters
                results.append(dict(session))
            return results

    except Exception as e:
        logger.error("Failed to search for contextual sessions", error=str(e), exc_info=True)
        return []


@tool
async def fetch_character_details(
    character_id: str, trace_id: Optional[str] = None, session_id: Optional[str] = None
) -> Optional[Dict]:
    """Fetches the full details of a specific character from the database by its ID."""
    logger.info("Fetching character details", character_id=character_id)
    try:
        async with async_session_factory() as db:
            character = await db.get(Character, character_id)
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


# Instantiate the search tools once to be reused.
# Note: These require TAVILY_API_KEY and appropriate python packages.
web_search = TavilySearchResults(max_results=3)
wikipedia_search = WikipediaAPIWrapper(top_k_results=2, doc_content_chars_max=2000)

# To make them LangChain tools, we can wrap them if needed or just use them.
# For simplicity, we'll expose them directly. The @tool decorator is not
# strictly necessary if we register the instantiated objects.
web_search.name = "web_search"
web_search.description = "A powerful web search engine. Use for current events or general knowledge."
wikipedia_search.name = "wikipedia_search"
wikipedia_search.description = "Searches for a term on Wikipedia. Good for factual, encyclopedic information."
