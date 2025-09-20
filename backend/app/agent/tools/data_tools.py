# backend/app/agent/tools/data_tools.py
"""
Agent Tools: Data Retrieval (RAG, Web Search, DB lookups)

- Vector RAG over prior sessions (pgvector)
- Character fetch by ID
- Wikipedia search (lightweight, local)
- Web search via LiteLLM (optional; falls back gracefully)

These tools are tolerant (non-blocking on failure) and log richly for diagnosis.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import litellm
import structlog
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langchain_community.utilities.wikipedia import WikipediaAPIWrapper
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.config import settings
from app.models.db import Character  # avoids circulars
from app.services.llm_service import llm_service

logger = structlog.get_logger(__name__)

# -------------------------
# Pydantic Inputs
# -------------------------

class SynopsisInput(BaseModel):
    """Input for contextual session search."""
    category_synopsis: str = Field(description="Detailed synopsis for the quiz category.")

class CharacterInput(BaseModel):
    """Input for fetching character details."""
    character_id: str = Field(description="UUID of the character to fetch.")

# -------------------------
# Vector RAG over prior sessions
# -------------------------

@tool
async def search_for_contextual_sessions(
    tool_input: SynopsisInput,
    config: RunnableConfig,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Semantic vector search over prior sessions using pgvector.

    Returns: List[dict] nearest-first. On any error, returns [] (non-blocking).
    """
    preview = (tool_input.category_synopsis or "")[:120]
    logger.info("tool.search_for_contextual_sessions.start", synopsis_preview=preview)

    db_session: Optional[AsyncSession] = config.get("configurable", {}).get("db_session")  # type: ignore
    if not db_session:
        logger.warning("tool.search_for_contextual_sessions.nodb")
        return []

    # 1) Embed the query text (tolerant)
    try:
        embs = await llm_service.get_embedding(input=[tool_input.category_synopsis])
        if not embs or not isinstance(embs[0], list) or not embs[0]:
            logger.warning("tool.search_for_contextual_sessions.no_embedding")
            return []
        query_vector: List[float] = embs[0]
    except Exception as e:
        logger.error("tool.search_for_contextual_sessions.embed_fail", error=str(e), exc_info=True)
        return []

    # 2) Vector search
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
        k = 5
        async with db_session as db:
            result = await db.execute(sql, {"qvec": query_vector, "k": k})
            rows = result.mappings().all()  # type: ignore[attr-defined]

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
                # Skip malformed rows but proceed
                continue

        logger.info("tool.search_for_contextual_sessions.ok", hits=len(hits))
        return hits
    except Exception as e:
        logger.error("tool.search_for_contextual_sessions.query_fail", error=str(e), exc_info=True)
        return []

# -------------------------
# Character fetch by ID
# -------------------------

@tool
async def fetch_character_details(
    tool_input: CharacterInput,
    config: RunnableConfig,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Fetch full character details by ID. Returns None on not found or error.
    """
    logger.info("tool.fetch_character_details.start", character_id=tool_input.character_id)

    db_session: Optional[AsyncSession] = config.get("configurable", {}).get("db_session")  # type: ignore
    if not db_session:
        logger.warning("tool.fetch_character_details.nodb")
        return None

    try:
        async with db_session as db:
            result = await db.execute(select(Character).filter_by(id=tool_input.character_id))
            character = result.scalars().first()
            if not character:
                logger.info("tool.fetch_character_details.miss", character_id=tool_input.character_id)
                return None
            payload = {
                "id": str(character.id),
                "name": character.name,
                "profile_text": character.profile_text,
                "short_description": character.short_description,
            }
            logger.info("tool.fetch_character_details.ok", name=character.name)
            return payload
    except Exception as e:
        logger.error("tool.fetch_character_details.fail", error=str(e), exc_info=True)
        return None

# -------------------------
# Wikipedia (simple, local)
# -------------------------

_wikipedia_search = WikipediaAPIWrapper(top_k_results=2, doc_content_chars_max=2000)

@tool
def wikipedia_search(query: str) -> str:
    """
    Searches for a term on Wikipedia (encyclopedic info).
    Returns "" on error.
    """
    logger.info("tool.wikipedia_search.start", query=query)
    try:
        result = _wikipedia_search.run(query) or ""
        logger.info("tool.wikipedia_search.ok", has_result=bool(result))
        return result
    except Exception as e:
        logger.error("tool.wikipedia_search.fail", error=str(e), exc_info=True)
        return ""

# -------------------------
# Web Search via LiteLLM (optional)
# -------------------------

@tool
async def web_search(
    query: str,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> str:
    """
    General-purpose web search using a search-enabled model.

    - If `settings.llm_tools["web_search"]` exists, we honor its model/params.
    - Otherwise fallback to "openai/gpt-4o-search-preview".
    - Returns a concise, plain-text answer. On error, returns "".
    """
    cfg = settings.llm_tools.get("web_search")
    model_name = cfg.model if cfg else "openai/gpt-4o-search-preview"
    temperature = (cfg.temperature if cfg else 0.2)  # keep concise
    max_tokens = (cfg.max_output_tokens if cfg else 800)
    timeout_s = (cfg.timeout_s if cfg else 20)

    messages = [
        {
            "role": "system",
            "content": (
                "You are a precise research assistant. Use web search to find current, "
                "authoritative information. Return a compact summary with key points. "
                "If you cite, include short source names and URLs inline."
            ),
        },
        {"role": "user", "content": query},
    ]

    kwargs: Dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "timeout": timeout_s,
        "metadata": {"tool_name": "web_search", "trace_id": trace_id, "session_id": session_id},
        "web_search_options": {"search_context_size": "medium"},
    }

    logger.info("tool.web_search.start", model=model_name)
    try:
        resp = await litellm.acompletion(**kwargs)
        content = resp.choices[0].message.content
        text = content if isinstance(content, str) else ""
        logger.info("tool.web_search.ok", has_content=bool(text))
        return text
    except litellm.exceptions.ContentPolicyViolationError as e:
        logger.warning("tool.web_search.blocked", error=str(e))
        return ""
    except Exception as e:
        logger.error("tool.web_search.fail", error=str(e), exc_info=True)
        return ""
