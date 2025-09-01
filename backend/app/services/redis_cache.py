"""
Redis Cache Service (Repository Pattern)

This service module encapsulates all interactions with the Redis cache. It is
structured as a repository to provide a clean, high-level API for managing
short-term session state and caching expensive RAG results.

All functions are asynchronous and expect a Redis client instance to be provided
via dependency injection.
"""

import uuid
from typing import Optional

import redis.asyncio as redis
import structlog

# The agent's state is defined as a TypedDict for in-memory operations.
from app.agent.state import GraphState
# For robust serialization to/from Redis, we use a Pydantic model that mirrors the state.
from app.models.api import PydanticGraphState

logger = structlog.get_logger(__name__)


class CacheRepository:
    """Handles all Redis cache operations."""

    def __init__(self, client: redis.Redis):
        """Initializes the repository with a Redis client."""
        self.client = client

    async def save_quiz_state(
        self, state: GraphState, ttl_seconds: int = 3600
    ) -> None:
        """
        Saves the state of a quiz session to Redis with a specified TTL.

        Args:
            state: The agent's GraphState dictionary.
            ttl_seconds: The time-to-live for the cache entry, in seconds.
        """
        session_id = state.get("session_id")
        if not session_id:
            logger.warning("Attempted to save state without a session_id.")
            return

        session_key = f"quiz_session:{session_id}"
        
        # Validate the dictionary against the Pydantic model for safe serialization.
        state_pydantic = PydanticGraphState.model_validate(state)
        state_json = state_pydantic.model_dump_json()
        
        await self.client.set(session_key, state_json, ex=ttl_seconds)
        logger.info("Saved quiz state to Redis.", session_id=session_id)

    async def get_quiz_state(self, session_id: uuid.UUID) -> Optional[GraphState]:
        """
        Retrieves and safely deserializes a quiz session state from Redis.

        Args:
            session_id: The unique identifier for the quiz session.

        Returns:
            The agent's GraphState dictionary if found, otherwise None.
        """
        session_key = f"quiz_session:{session_id}"
        state_json = await self.client.get(session_key)
        
        if state_json:
            # FIX: Redis returns bytes; decode to str before Pydantic JSON validation.
            if isinstance(state_json, (bytes, bytearray)):
                state_json = state_json.decode("utf-8")
            # Use the Pydantic model for safe deserialization.
            pydantic_state = PydanticGraphState.model_validate_json(state_json)
            # Return as a dictionary to match the GraphState TypedDict format.
            return pydantic_state.model_dump()
            
        logger.warning("Quiz state not found in Redis.", session_id=session_id)
        return None

    async def update_quiz_state_atomically(
        self, session_id: uuid.UUID, new_data: dict
    ) -> Optional[GraphState]:
        """
        Atomically updates a quiz session state using a WATCH/MULTI/EXEC transaction
        to prevent race conditions.

        Args:
            session_id: The unique identifier for the quiz session.
            new_data: A dictionary of new data to update in the state.

        Returns:
            The updated GraphState dictionary, or None if the session expired.
        """
        session_key = f"quiz_session:{session_id}"
        async with self.client.pipeline() as pipe:
            while True:
                try:
                    await pipe.watch(session_key)
                    state_json_bytes = await pipe.get(session_key)
                    if not state_json_bytes:
                        logger.error("Quiz session not found or expired during atomic update.", session_id=session_id)
                        return None

                    state_json = state_json_bytes.decode('utf-8')
                    current_pydantic_state = PydanticGraphState.model_validate_json(state_json)
                    
                    # Merge the old state with the new data.
                    current_state_dict = current_pydantic_state.model_dump()
                    current_state_dict.update(new_data)

                    # Validate the updated state and serialize it.
                    updated_pydantic_state = PydanticGraphState.model_validate(current_state_dict)
                    updated_state_json = updated_pydantic_state.model_dump_json()

                    # Execute the transaction.
                    pipe.multi()
                    pipe.set(session_key, updated_state_json)
                    pipe.expire(session_key, 3600) # Reset TTL on update
                    await pipe.execute()
                    
                    logger.info("Atomically updated quiz state in Redis.", session_id=session_id)
                    return updated_pydantic_state.model_dump()
                
                except redis.WatchError:
                    # If another client modified the key, retry the transaction.
                    logger.warning("WatchError during atomic update, retrying...", session_id=session_id)
                    continue

    async def get_rag_cache(self, category_slug: str) -> Optional[str]:
        """
        Attempts to retrieve a cached RAG result for a given category.

        Args:
            category_slug: The URL-friendly slug for the category.

        Returns:
            The cached RAG result string if found, otherwise None.
        """
        cache_key = f"rag_cache:{category_slug}"
        raw = await self.client.get(cache_key)
        # FIX: Decode bytes to str for callers expecting text.
        if isinstance(raw, (bytes, bytearray)):
            return raw.decode("utf-8")
        return raw

    async def set_rag_cache(
        self, category_slug: str, rag_result: str, ttl_seconds: int = 86400
    ) -> None:
        """
        Caches a RAG result with a specified TTL.

        Args:
            category_slug: The URL-friendly slug for the category.
            rag_result: The string content of the RAG result to cache.
            ttl_seconds: The time-to-live for the cache entry (defaults to 24 hours).
        """
        cache_key = f"rag_cache:{category_slug}"
        await self.client.set(cache_key, rag_result, ex=ttl_seconds)
