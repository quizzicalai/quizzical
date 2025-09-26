# services/database.py
"""
Database Service (Repository Pattern) — BYPASS MODE

This version intentionally bypasses ALL database I/O so the application
can run background tasks without a live DB connection.

- All write operations are no-ops (logged + skipped).
- All read operations return safe defaults (None / []), never touching the DB.
- Original DB code paths are preserved as commented blocks for easy restore.

When you're ready to re-enable persistence, uncomment the marked sections.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

import structlog
from fastapi import Depends

# NOTE: We keep these imports to preserve the original API surface,
# but we do not use them while DB is bypassed.
from sqlalchemy import select, text  # noqa: F401
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: F401

from app.api.dependencies import get_db_session
from app.models.api import FeedbackRatingEnum, ShareableResultResponse
from app.models.db import Character, SessionHistory, UserSentimentEnum  # noqa: F401

logger = structlog.get_logger(__name__)

# Placeholder until a dedicated GraphState type lives here.
GraphState = dict

# --- Hybrid search SQL preserved for future use (unused in bypass mode) ---
HYBRID_SEARCH_FOR_SESSIONS_SQL = text(
    """
    WITH semantic_search AS (
        SELECT session_id, row_number() OVER (ORDER BY synopsis_embedding <=> :query_vector) AS rank
        FROM session_history ORDER BY synopsis_embedding <=> :query_vector LIMIT :search_limit
    ),
    keyword_search AS (
        SELECT session_id, row_number() OVER () AS rank
        FROM session_history WHERE synopsis_tsv @@ websearch_to_tsquery('english', :query_text) LIMIT :search_limit
    ),
    rrf_scores AS (
        SELECT session_id, 1.0 / (:rrf_k + rank) as rrf_score FROM semantic_search
        UNION ALL
        SELECT session_id, 1.0 / (:rrf_k + rank) as rrf_score FROM keyword_search
    ),
    ranked_sessions AS (
        SELECT session_id
        FROM rrf_scores
        GROUP BY session_id
        ORDER BY SUM(rrf_score) DESC
        LIMIT :k
    )
    SELECT
        s.session_id,
        s.category,
        s.category_synopsis,
        s.agent_plan,
        s.session_transcript,
        s.final_result,
        s.judge_plan_score,
        s.judge_plan_feedback,
        s.user_sentiment,
        s.user_feedback_text
    FROM session_history s
    JOIN ranked_sessions ON s.session_id = ranked_sessions.session_id;
"""
)


# =============================================================================
# CharacterRepository (BYPASS)
# =============================================================================
class CharacterRepository:
    """Handles all database operations for the Character model (bypassed)."""

    def __init__(self, session: AsyncSession):
        # Keep the attribute to avoid breaking call sites, but do not use it.
        self.session = session

    async def get_by_id(self, character_id: uuid.UUID) -> Character | None:
        """
        BYPASS: Do not hit the DB; return None.
        """
        logger.debug("DB BYPASS: CharacterRepository.get_by_id", character_id=str(character_id))
        # Original (restore when enabling DB):
        # return await self.session.get(Character, character_id)
        return None

    async def get_many_by_ids(self, character_ids: List[uuid.UUID]) -> List[Character]:
        """
        BYPASS: Do not hit the DB; return [].
        """
        logger.debug(
            "DB BYPASS: CharacterRepository.get_many_by_ids", count=len(character_ids or [])
        )
        # Original:
        # if not character_ids:
        #     return []
        # stmt = select(Character).where(Character.id.in_(character_ids))
        # result = await self.session.execute(stmt)
        # return result.scalars().all()
        return []

    async def create(self, name: str, **kwargs) -> Character:
        """
        BYPASS: Return an in-memory Character stub without persisting.
        """
        logger.info("DB BYPASS: CharacterRepository.create (stubbed return)", name=name, kwargs=kwargs)
        # Original:
        # new_character = Character(name=name, **kwargs)
        # async with self.session.begin():
        #     self.session.add(new_character)
        # await self.session.refresh(new_character)
        # return new_character
        # Stub (not persisted). If your Character model requires more fields,
        # pass them via **kwargs at call sites or extend this stub as needed.
        return Character(id=uuid.uuid4(), name=name, **kwargs)

    async def update_profile(
        self, character_id: uuid.UUID, new_profile_text: str
    ) -> Character | None:
        """
        BYPASS: No-op; return None.
        """
        logger.info(
            "DB BYPASS: CharacterRepository.update_profile (no-op)",
            character_id=str(character_id),
        )
        # Original:
        # async with self.session.begin():
        #     character = await self.session.get(Character, character_id)
        #     if not character:
        #         return None
        #     character.profile_text = new_profile_text
        #     character.judge_quality_score = None
        # await self.session.refresh(character)
        # return character
        return None


# =============================================================================
# SessionRepository (BYPASS)
# =============================================================================
class SessionRepository:
    """Handles all database operations for the SessionHistory model (bypassed)."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def find_relevant_sessions_for_rag(
        self,
        query_text: str,
        query_vector: List[float],
        k: int = 5,
        search_limit: int = 50,
        rrf_k: int = 60,
    ) -> List[dict]:
        """
        BYPASS: Do not query DB; return an empty list.
        """
        logger.debug(
            "DB BYPASS: SessionRepository.find_relevant_sessions_for_rag",
            k=k,
            search_limit=search_limit,
            rrf_k=rrf_k,
        )
        # Original:
        # result = await self.session.execute(
        #     HYBRID_SEARCH_FOR_SESSIONS_SQL,
        #     {
        #         "query_vector": str(query_vector),
        #         "query_text": query_text,
        #         "k": k,
        #         "search_limit": search_limit,
        #         "rrf_k": rrf_k,
        #     },
        # )
        # return result.mappings().all()
        return []

    async def save_feedback(
        self,
        session_id: uuid.UUID,
        rating: FeedbackRatingEnum,
        feedback_text: str | None,
    ) -> Optional[SessionHistory]:
        """
        BYPASS: No-op; return None.
        """
        logger.info(
            "DB BYPASS: SessionRepository.save_feedback (no-op)",
            session_id=str(session_id),
            rating=rating.value if hasattr(rating, "value") else str(rating),
        )
        # Original:
        # async with self.session.begin():
        #     session = await self.session.get(SessionHistory, session_id)
        #     if not session:
        #         return None
        #     session.user_sentiment = (
        #         UserSentimentEnum.POSITIVE
        #         if rating == FeedbackRatingEnum.UP
        #         else UserSentimentEnum.NEGATIVE
        #     )
        #     if feedback_text:
        #         session.user_feedback_text = feedback_text
        # await self.session.refresh(session)
        # return session
        return None

    async def create_from_agent_state(self, state: GraphState) -> Optional[SessionHistory]:
        """
        BYPASS: Do not create a DB record; return None.
        (We still log what would have been saved for debugging.)
        """
        logger.info(
            "DB BYPASS: SessionRepository.create_from_agent_state (no-op)",
            session_id=str(state.get("quiz_id") or state.get("session_id")),
            has_final_result=bool(state.get("final_result")),
        )
        # Original logic preserved for future re-enable:
        # try:
        #     final_result = state.get("final_result")
        #     if hasattr(final_result, "model_dump"):
        #         final_result = final_result.model_dump()
        #     session_data = {
        #         "session_id": state["quiz_id"],
        #         "category": state["category"],
        #         "category_synopsis": state["category_synopsis"],
        #         "synopsis_embedding": state.get("synopsis_embedding"),
        #         "agent_plan": state.get("agent_plan"),
        #         "session_transcript": state.get("quiz_history", []),
        #         "final_result": final_result,
        #     }
        # except KeyError as e:
        #     raise ValueError(f"Cannot create session history: missing required key {e}")
        #
        # new_session = SessionHistory(**session_data)
        # async with self.session.begin():
        #     self.session.add(new_session)
        # await self.session.refresh(new_session)
        # return new_session
        return None


# =============================================================================
# ResultService (BYPASS)
# =============================================================================
class ResultService:
    """
    Handles business logic related to retrieving and presenting quiz results.
    BYPASS: Always returns None to avoid DB access. The v0 app renders results
    from Redis-backed /quiz/status and never depends on DB persistence.
    """

    def __init__(self, session: AsyncSession = Depends(get_db_session)):
        # Keep attribute for compatibility; unused in bypass mode.
        self.session = session

    async def get_result_by_id(self, result_id: uuid.UUID) -> Optional[ShareableResultResponse]:
        """
        BYPASS: Do not hit the DB; return None.
        """
        logger.debug("DB BYPASS: ResultService.get_result_by_id", result_id=str(result_id))
        # Original (restore when enabling DB):
        # session_record = await self.session.get(SessionHistory, result_id)
        # if not session_record or not session_record.final_result:
        #     return None
        # normalized = normalize_final_result(session_record.final_result)
        # if not normalized:
        #     return None
        # return ShareableResultResponse(
        #     title=normalized.get("title", ""),
        #     description=normalized.get("description", ""),
        #     image_url=normalized.get("image_url"),
        #     category=session_record.category,
        #     created_at=getattr(session_record, "created_at", None),
        # )
        return None


# =============================================================================
# Helpers (still useful with or without DB)
# =============================================================================
def normalize_final_result(raw_result: Any) -> Optional[Dict[str, str]]:
    """
    Normalize various formats of final_result into a consistent dict.
    This helper is DB-agnostic and retained as-is.

    Returns:
        dict with keys: title, description, image_url
        or None if it cannot be normalized.
    """
    if not raw_result:
        return None

    # Pydantic v2
    if hasattr(raw_result, "model_dump"):
        try:
            raw_result = raw_result.model_dump()
        except Exception:
            pass
    # Pydantic v1 / dataclasses-like
    elif hasattr(raw_result, "dict"):
        try:
            raw_result = raw_result.dict()
        except Exception:
            pass

    # If the agent stored a plain text description
    if isinstance(raw_result, str):
        return {
            "title": "Quiz Result",
            "description": raw_result,
            "image_url": "",
        }

    # Common dict shapes
    if isinstance(raw_result, dict):
        # Accept multiple common field names and coerce to our canonical keys.
        title = (
            raw_result.get("profileTitle")
            or raw_result.get("title")
            or "Quiz Result"
        )
        description = (
            raw_result.get("summary")
            or raw_result.get("description")
            or ""
        )
        image_url = raw_result.get("image_url") or raw_result.get("imageUrl") or ""

        return {
            "title": title,
            "description": description,
            "image_url": image_url,
        }

    logger.warning("Could not normalize final_result", type=type(raw_result).__name__)
    return None
