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

# The GraphState is a TypedDict, not a Pydantic model. We'll handle serialization
# manually using a helper or directly within the methods.
from app.agent.state import GraphState
from app.models.api import PydanticGraphState

logger = structlog.get_logger(__name__)


class CacheRepository:
    """Handles all Redis cache operations."""

    def __init__(self, client: redis.Redis):
        self.client = client

    async def save_quiz_state(
        self, state: GraphState, ttl_seconds: int = 3600
    ) -> None:
        """
        Saves the state of a quiz session to Redis with a specified TTL.
        """
        # FIX: Use 'session_id' from the state dictionary.
        session_id = state.get("session_id")
        if not session_id:
            logger.warning("Attempted to save state without a session_id.")
            return

        session_key = f"quiz_session:{session_id}"
        # Use the Pydantic model for safe serialization
        state_pydantic = PydanticGraphState.model_validate(state)
        state_json = state_pydantic.model_dump_json()
        await self.client.set(session_key, state_json, ex=ttl_seconds)

    async def get_quiz_state(self, session_id: uuid.UUID) -> Optional[GraphState]:
        """
        Retrieves and safely deserializes a quiz session state from Redis.
        """
        # FIX: Parameter renamed to session_id for clarity.
        session_key = f"quiz_session:{session_id}"
        state_json = await self.client.get(session_key)
        if state_json:
            # Use the Pydantic model for safe deserialization
            pydantic_state = PydanticGraphState.model_validate_json(state_json)
            # Return as a dictionary to match the GraphState TypedDict
            return pydantic_state.model_dump()
        return None

    async def update_quiz_state_atomically(
        self, session_id: uuid.UUID, new_data: dict
    ) -> GraphState:
        """
        Atomically updates a quiz session state using a WATCH/MULTI/EXEC transaction
        to prevent race conditions.
        """
        # FIX: Parameter renamed to session_id.
        session_key = f"quiz_session:{session_id}"
        async with self.client.pipeline() as pipe:
            while True:
                try:
                    await pipe.watch(session_key)
                    state_json_bytes = await pipe.get(session_key)
                    if not state_json_bytes:
                        raise ValueError("Quiz session not found or expired.")

                    # Redis client with decode_responses=False returns bytes
                    state_json = state_json_bytes.decode('utf-8')
                    current_pydantic_state = PydanticGraphState.model_validate_json(state_json)
                    
                    # Create a new state dictionary and update it
                    current_state_dict = current_pydantic_state.model_dump()
                    current_state_dict.update(new_data)

                    # Validate the updated dictionary back into a Pydantic model
                    updated_pydantic_state = PydanticGraphState.model_validate(current_state_dict)
                    updated_state_json = updated_pydantic_state.model_dump_json()

                    pipe.multi()
                    pipe.set(session_key, updated_state_json)
                    pipe.expire(session_key, 3600)
                    await pipe.execute()
                    
                    return updated_pydantic_state.model_dump()
                except redis.WatchError:
                    continue

    async def get_rag_cache(self, category_slug: str) -> Optional[str]:
        """
        Attempts to retrieve a cached RAG result.
        """
        cache_key = f"rag_cache:{category_slug}"
        return await self.client.get(cache_key)

    async def set_rag_cache(
        self, category_slug: str, rag_result: str, ttl_seconds: int = 86400
    ) -> None:
        """
        Caches a RAG result with a specified TTL (defaults to 24 hours).
        """
        cache_key = f"rag_cache:{category_slug}"
        await self.client.set(cache_key, rag_result, ex=ttl_seconds)
