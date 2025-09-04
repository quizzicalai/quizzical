"""
Database Service (Repository Pattern)

This service module encapsulates all interactions with the PostgreSQL database.
It is structured using the Repository Pattern, where each class corresponds to
a specific database model and contains all the logic for interacting with it.

This pattern improves organization, testability, and maintainability by grouping
related database operations together.
"""

import uuid
from typing import Any, Dict, List, Optional

import structlog
from fastapi import Depends
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db_session
from app.models.api import FeedbackRatingEnum, ShareableResultResponse
from app.models.db import Character, SessionHistory, UserSentimentEnum

logger = structlog.get_logger(__name__)

# NOTE: The GraphState model will be defined in `app.agent.state`.
# We are using a placeholder `dict` here until that file is created.
# from app.agent.state import GraphState
GraphState = dict

# This query performs a full hybrid search (semantic + keyword with RRF)
# to find the most relevant SESSIONS. All key tuning parameters are now
# exposed as bind parameters (:search_limit, :rrf_k, :k) to allow for
# dynamic adjustment from the application layer.
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


class CharacterRepository:
    """Handles all database operations for the Character model."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, character_id: uuid.UUID) -> Character | None:
        """Retrieves a single character by its primary key."""
        return await self.session.get(Character, character_id)

    async def get_many_by_ids(self, character_ids: List[uuid.UUID]) -> List[Character]:
        """Retrieves multiple characters by their primary keys efficiently."""
        if not character_ids:
            return []
        stmt = select(Character).where(Character.id.in_(character_ids))
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def create(self, name: str, **kwargs) -> Character:
        """
        Creates a new character. This is called when the LLM planner decides
        a character is new and does not exist in the context it was given.
        """
        new_character = Character(name=name, **kwargs)
        async with self.session.begin():
            self.session.add(new_character)
        await self.session.refresh(new_character)
        return new_character

    async def update_profile(
        self, character_id: uuid.UUID, new_profile_text: str
    ) -> Character | None:
        """
        Updates the profile text and resets the quality score for a character.
        This is called when the agent decides an existing character needs to be improved.
        """
        async with self.session.begin():
            character = await self.session.get(Character, character_id)
            if not character:
                return None
            character.profile_text = new_profile_text
            # When a profile is improved, its old score is no longer valid.
            # Setting it to NULL signals that it needs to be re-judged.
            character.judge_quality_score = None
        await self.session.refresh(character)
        return character


class SessionRepository:
    """Handles all database operations for the SessionHistory model."""

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
        Performs a parameterized hybrid search to find the most relevant SESSIONS
        from past quizzes to provide as rich context to the LLM planner.
        """
        result = await self.session.execute(
            HYBRID_SEARCH_FOR_SESSIONS_SQL,
            {
                "query_vector": str(query_vector),
                "query_text": query_text,
                "k": k,
                "search_limit": search_limit,
                "rrf_k": rrf_k,
            },
        )
        return result.mappings().all()

    async def save_feedback(
        self,
        session_id: uuid.UUID,
        rating: FeedbackRatingEnum,
        feedback_text: str | None,
    ) -> SessionHistory | None:
        """Updates a session with user-provided feedback."""
        async with self.session.begin():
            session = await self.session.get(SessionHistory, session_id)
            if not session:
                return None
            session.user_sentiment = (
                UserSentimentEnum.POSITIVE
                if rating == FeedbackRatingEnum.UP
                else UserSentimentEnum.NEGATIVE
            )
            if feedback_text:
                session.user_feedback_text = feedback_text
        await self.session.refresh(session)
        return session

    async def create_from_agent_state(self, state: GraphState) -> SessionHistory:
        """
        Creates a new SessionHistory record from the final agent state.
        This function is defensive and ensures all required data is present.
        
        FIXED: Now saves the complete final_result object instead of just the description.
        """
        try:
            # Extract and validate the final_result
            final_result = state.get("final_result")
            if not final_result:
                logger.warning(
                    "No final_result in state when creating session history",
                    session_id=state.get("quiz_id")
                )
                final_result = None
            elif isinstance(final_result, dict):
                # Validate it has the required fields
                if not all(k in final_result for k in ["title", "description", "image_url"]):
                    logger.warning(
                        "final_result missing required fields",
                        session_id=state.get("quiz_id"),
                        keys=list(final_result.keys())
                    )
            elif hasattr(final_result, "model_dump"):
                # If it's a Pydantic model, convert to dict
                final_result = final_result.model_dump()
            
            session_data = {
                "session_id": state["quiz_id"],
                "category": state["category"],
                "category_synopsis": state["category_synopsis"],
                "synopsis_embedding": state.get("synopsis_embedding"),  # Make optional
                "agent_plan": state.get("agent_plan"),  # Make optional
                "session_transcript": state.get("quiz_history", []),  # Provide default
                "final_result": final_result,  # Save the FULL object
            }
        except KeyError as e:
            raise ValueError(f"Cannot create session history: missing required key {e}")

        new_session = SessionHistory(**session_data)
        async with self.session.begin():
            self.session.add(new_session)
        await self.session.refresh(new_session)
        
        logger.info(
            "Created session history with complete final_result",
            session_id=str(new_session.session_id),
            has_final_result=bool(new_session.final_result)
        )
        return new_session


class ResultService:
    """
    Handles business logic related to retrieving and presenting quiz results.
    This service is injectable into FastAPI endpoints.
    """

    def __init__(self, session: AsyncSession = Depends(get_db_session)):
        """
        Initializes the service with a database session provided by FastAPI's
        dependency injection system.
        """
        self.session = session

    async def get_result_by_id(self, result_id: uuid.UUID) -> ShareableResultResponse | None:
        """
        Retrieves a shareable quiz result by its session ID.

        This method fetches the completed session from the database and formats
        the `final_result` JSONB field into the `ShareableResultResponse`
        Pydantic model that the frontend expects.
        
        FIXED: Now handles both old (broken) string format and new (correct) dict format
        for backward compatibility.
        """
        # Retrieve the session history record using its primary key (session_id)
        session_record = await self.session.get(SessionHistory, result_id)

        # If the record doesn't exist or if the agent never stored a final result,
        # there's nothing to show.
        if not session_record or not session_record.final_result:
            logger.info(
                "Result not found or has no final_result",
                result_id=str(result_id),
                found=bool(session_record),
                has_final_result=bool(session_record.final_result if session_record else False)
            )
            return None

        # Handle backward compatibility with old format
        final_result = session_record.final_result
        
        # Case 1: Old broken format where only description was saved as a string
        if isinstance(final_result, str):
            logger.warning(
                "Found legacy string-only final_result, constructing minimal response",
                result_id=str(result_id)
            )
            return ShareableResultResponse(
                title="Quiz Result",  # Default title for legacy data
                description=final_result,
                image_url=""  # Empty image for legacy data
            )
        
        # Case 2: Proper dict format
        if isinstance(final_result, dict):
            # Ensure all required fields are present
            if not all(k in final_result for k in ["title", "description", "image_url"]):
                logger.warning(
                    "final_result dict missing required fields, using defaults",
                    result_id=str(result_id),
                    fields=list(final_result.keys())
                )
                # Construct with defaults for missing fields
                return ShareableResultResponse(
                    title=final_result.get("title", "Quiz Result"),
                    description=final_result.get("description", ""),
                    image_url=final_result.get("image_url", "")
                )
            
            # Normal case: validate the complete object
            try:
                return ShareableResultResponse.model_validate(final_result)
            except Exception as e:
                logger.error(
                    "Failed to validate final_result",
                    result_id=str(result_id),
                    error=str(e),
                    final_result=final_result
                )
                # Fall back to manual construction
                return ShareableResultResponse(
                    title=final_result.get("title", "Quiz Result"),
                    description=final_result.get("description", ""),
                    image_url=final_result.get("image_url", "")
                )
        
        # Case 3: Unexpected format
        logger.error(
            "Unexpected final_result format",
            result_id=str(result_id),
            type=type(final_result).__name__
        )
        return None


def normalize_final_result(raw_result: Any) -> Optional[Dict[str, str]]:
    """
    Helper function to normalize various formats of final_result into a consistent dict.
    Used by both SessionRepository and ResultService for data consistency.
    
    Args:
        raw_result: The raw final_result from various sources (agent state, database, etc.)
    
    Returns:
        A normalized dict with title, description, and image_url, or None if invalid.
    """
    if not raw_result:
        return None
    
    # If it's a Pydantic model, convert to dict
    if hasattr(raw_result, "model_dump"):
        raw_result = raw_result.model_dump()
    elif hasattr(raw_result, "dict"):
        try:
            raw_result = raw_result.dict()
        except Exception:
            pass
    
    # If it's a string (legacy format), wrap it
    if isinstance(raw_result, str):
        return {
            "title": "Quiz Result",
            "description": raw_result,
            "image_url": ""
        }
    
    # If it's a dict, ensure it has all required fields
    if isinstance(raw_result, dict):
        return {
            "title": raw_result.get("title", "Quiz Result"),
            "description": raw_result.get("description", ""),
            "image_url": raw_result.get("image_url", "")
        }
    
    # Unknown format
    logger.warning(
        "Could not normalize final_result",
        type=type(raw_result).__name__
    )
    return None