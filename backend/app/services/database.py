"""
Database Service (Repository Pattern)

This service module encapsulates all interactions with the PostgreSQL database.
It is structured using the Repository Pattern, where each class corresponds to
a specific database model and contains all the logic for interacting with it.

This pattern improves organization, testability, and maintainability by grouping
related database operations together.
"""

import uuid
from typing import List

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api import FeedbackRatingEnum
from app.models.db import Character, SessionHistory, UserSentimentEnum

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

        Args:
            query_text: The user's category for keyword search.
            query_vector: The embedding of the category synopsis for semantic search.
            k: The final number of sessions to return.
            search_limit: The number of results to fetch from each search method
                          before fusion.
            rrf_k: The ranking constant for Reciprocal Rank Fusion. A smaller
                   value gives more weight to top-ranked items.
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
        """
        try:
            session_data = {
                "session_id": state["quiz_id"],
                "category": state["category"],
                "category_synopsis": state["category_synopsis"],
                "synopsis_embedding": state["synopsis_embedding"],
                "agent_plan": state["agent_plan"],
                "session_transcript": state["quiz_history"],
                "final_result": state["final_result"]["description"],
            }
        except KeyError as e:
            # This provides a clear error if the agent state is missing data.
            raise ValueError(f"Cannot create session history: missing required key {e}")

        new_session = SessionHistory(**session_data)
        async with self.session.begin():
            self.session.add(new_session)
        await self.session.refresh(new_session)
        return new_session
