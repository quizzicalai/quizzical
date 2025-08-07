"""
Agent Tools: Data & Research

This module contains tools for interacting with data sources, both internal
(our database) and external (web search, Wikipedia).

NOTE: The web search tools require the `tavily-python` package to be installed
(`poetry add tavily-python`) and the `TAVILY_API_KEY` secret to be configured.
The RAG tool requires `sentence-transformers` (`poetry add sentence-transformers`).
"""
import uuid
from typing import Dict, List

import structlog
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from tavily import TavilyClient

from app.core.config import settings
from app.services.database import CharacterRepository, SessionRepository
from app.services.llm_service import llm_service

logger = structlog.get_logger(__name__)

# --- Pydantic Models for Structured Inputs ---


class SearchContextInput(BaseModel):
    """Input for the search_for_contextual_sessions tool."""

    category_synopsis: str = Field(
        ..., description="The synopsis of the current quiz category."
    )


class FetchCharactersInput(BaseModel):
    """Input for the fetch_character_details tool."""

    character_ids: List[uuid.UUID] = Field(
        ..., description="A list of character UUIDs to fetch from the database."
    )


class WebSearchInput(BaseModel):
    """Input for the web_search and wikipedia_search tools."""

    query: str = Field(..., description="The search query.")


# --- Tool Definitions ---


@tool
async def search_for_contextual_sessions(
    input_data: SearchContextInput, db_session: AsyncSession
) -> List[Dict]:
    """
    Performs a semantic/hybrid search to find similar past quiz sessions
    and returns their full data for contextual analysis.
    """
    session_repo = SessionRepository(db_session)
    logger.info("Generating embedding for RAG search", query=input_data.category_synopsis[:100])

    # Generate embedding for the synopsis using a fast, local model via litellm
    # NOTE: Requires `sentence-transformers` to be installed.
    try:
        embedding_response = await llm_service.aembedding(
            model="sentence-transformers/all-MiniLM-L6-v2",
            input=[input_data.category_synopsis]
        )
        query_vector = embedding_response.data[0]['embedding']
    except Exception as e:
        logger.error("Failed to generate embedding for RAG", error=str(e))
        return []


    logger.info("Searching for relevant sessions in database")
    # Perform the hybrid search using the generated vector
    relevant_sessions = await session_repo.find_relevant_sessions_for_rag(
        query_text=input_data.category_synopsis, query_vector=query_vector
    )

    return [dict(session) for session in relevant_sessions]


@tool
async def fetch_character_details(
    input_data: FetchCharactersInput, db_session: AsyncSession
) -> List[Dict]:
    """
    Fetches the full details for a list of character UUIDs from the database.
    """
    if not input_data.character_ids:
        return []

    char_repo = CharacterRepository(db_session)
    logger.info("Fetching character details from database", character_ids=input_data.character_ids)
    characters = await char_repo.get_many_by_ids(input_data.character_ids)

    # Convert SQLAlchemy models to dictionaries for agent consumption
    return [
        {
            "id": str(c.id),
            "name": c.name,
            "short_description": c.short_description,
            "profile_text": c.profile_text,
        }
        for c in characters
    ]


@tool
async def web_search(input_data: WebSearchInput) -> str:
    """
    Performs a general web search to gather research on a topic using the Tavily API.
    """
    try:
        tavily = TavilyClient(api_key=settings.TAVILY_API_KEY.get_secret_value())
        logger.info("Performing Tavily web search", query=input_data.query)
        # We use `search` which is a comprehensive method.
        response = await tavily.search(
            query=input_data.query,
            search_depth="advanced",
            max_results=5,
        )
        # Concatenate the content from the search results into a single string
        return "\n".join([res["content"] for res in response["results"]])
    except Exception as e:
        logger.error("Tavily web search failed", query=input_data.query, error=str(e))
        return f"Error performing web search for '{input_data.query}'."


@tool
async def wikipedia_search(input_data: WebSearchInput) -> str:
    """
    Performs a search specifically against Wikipedia for factual information,
    by instructing the Tavily search tool to prioritize that domain.
    """
    # We can guide the search by adding "site:wikipedia.org" to the query.
    wikipedia_query = f"{input_data.query} site:wikipedia.org"
    try:
        tavily = TavilyClient(api_key=settings.TAVILY_API_KEY.get_secret_value())
        logger.info("Performing Tavily Wikipedia search", query=wikipedia_query)
        response = await tavily.search(
            query=wikipedia_query,
            search_depth="basic",
            max_results=3,
        )
        return "\n".join([res["content"] for res in response["results"]])
    except Exception as e:
        logger.error("Tavily Wikipedia search failed", query=wikipedia_query, error=str(e))
        return f"Error performing Wikipedia search for '{input_data.query}'."

