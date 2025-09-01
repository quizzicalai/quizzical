"""
Agent Tools: Data Retrieval (RAG, Web Search, etc.)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import structlog
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langchain_community.utilities.wikipedia import WikipediaAPIWrapper
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.llm_service import llm_service

logger = structlog.get_logger(__name__)


# --- Pydantic Models for Tool Inputs ---

class SynopsisInput(BaseModel):
    """Input schema for the contextual session search tool."""
    category_synopsis: str = Field(
        description="The detailed synopsis of the quiz category."
    )


class CharacterInput(BaseModel):
    """Input schema for the character detail fetching tool."""
    character_id: str = Field(description="The unique identifier (UUID) of the character to fetch.")


# --- Tool Definitions ---

@tool
async def search_for_contextual_sessions(
    tool_input: SynopsisInput,
    config: RunnableConfig,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Semantic vector search over prior sessions using pgvector.
    Returns an ordered list (nearest first) of lightweight session dicts.
    On any error, returns [] (non-blocking).
    """
    preview = (tool_input.category_synopsis or "")[:120]
    logger.info("RAG: searching for contextual sessions", synopsis_preview=preview)

    # Pull async db session from config
    db_session: Optional[AsyncSession] = config.get("configurable", {}).get("db_session")  # type: ignore
    if not db_session:
        logger.warn("RAG: no db_session in config; returning []")
        return []

    # 1) Embed the query text (tolerant to failure)
    try:
        embedding_response = await llm_service.get_embedding(
            input=[tool_input.category_synopsis]
        )
        query_vector: List[float] = embedding_response[0]
        if not isinstance(query_vector, list) or not query_vector:
            logger.warn("RAG: embedding response invalid/empty; returning []")
            return []
    except Exception as e:
        logger.error("RAG: embedding generation failed", error=str(e), exc_info=True)
        return []

    # 2) Vector search (pgvector cosine distance). Tolerant to DB/query errors.
    # NOTE: Requires `SessionHistory.synopsis_embedding` (VECTOR(dim), nullable)
    # and extension `CREATE EXTENSION IF NOT EXISTS vector;`
    sql = text(
        """
        SELECT
          session_id,
          category,
          category_synopsis,
          final_result,
          judge_plan_feedback,
          user_feedback_text,
          (synopsis_embedding <=> :qvec) AS distance
        FROM session_history
        WHERE synopsis_embedding IS NOT NULL
        ORDER BY synopsis_embedding <=> :qvec
        LIMIT :k
        """
    )

    try:
        # Reasonable default top-k for prompt context
        k = 5
        async with db_session as db:
            result = await db.execute(sql, {"qvec": query_vector, "k": k})
            rows = result.mappings().all()

        # Normalize payload to compact dicts consumable by prompts
        hits: List[Dict[str, Any]] = []
        for r in rows:
            try:
                hits.append(
                    {
                        "session_id": str(r.get("session_id")),
                        "category": r.get("category"),
                        "category_synopsis": r.get("category_synopsis"),
                        "final_result": r.get("final_result"),
                        "judge_feedback": r.get("judge_plan_feedback"),
                        "user_feedback": r.get("user_feedback_text"),
                        "distance": float(r.get("distance")) if r.get("distance") is not None else None,
                    }
                )
            except Exception:
                # Be extra-tolerant; skip malformed row
                continue

        logger.info("RAG: search complete", hits=len(hits))
        return hits
    except Exception as e:
        logger.error("RAG: vector search failed", error=str(e), exc_info=True)
        return []


# --- Character Fetch Tool (kept minimal, tolerant) ---

from sqlalchemy.future import select
from app.models.db import Character  # import after comments to avoid circulars

@tool
async def fetch_character_details(
    tool_input: CharacterInput,
    config: RunnableConfig,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Fetch full character details by ID. On any error/unknown ID, returns None.
    """
    logger.info("Fetching character details", character_id=tool_input.character_id)

    db_session: Optional[AsyncSession] = config.get("configurable", {}).get("db_session")  # type: ignore
    if not db_session:
        logger.warn("No db_session in config; returning None")
        return None

    try:
        async with db_session as db:
            result = await db.execute(select(Character).filter_by(id=tool_input.character_id))
            character = result.scalars().first()
            if not character:
                return None
            return {
                "id": str(character.id),
                "name": character.name,
                "profile_text": character.profile_text,
                "short_description": character.short_description,
            }
    except Exception as e:
        logger.error("Failed to fetch character details", error=str(e), exc_info=True)
        return None


# --- Web Search Tool Implementations ---

# Instantiate the Wikipedia search client once to be reused.
_wikipedia_search = WikipediaAPIWrapper(top_k_results=2, doc_content_chars_max=2000)

@tool
def wikipedia_search(query: str) -> str:
    """
    Searches for a term on Wikipedia. Good for factual, encyclopedic information.
    Input should be a specific term or topic to look up. Returns empty string on error.
    """
    logger.info("Performing Wikipedia search", query=query)
    try:
        return _wikipedia_search.run(query) or ""
    except Exception as e:
        logger.error("Wikipedia search failed", error=str(e), exc_info=True)
        return ""
