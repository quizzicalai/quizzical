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

# NOTE: The GraphState model will be defined in `app.agent.state`.
# We import it here to use it for type hinting and serialization.
# Pydantic's .model_dump_json() and .model_validate_json() are used
# for safe and efficient serialization, replacing the insecure `eval()`.
from app.agent.state import GraphState


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
        session_key = f"quiz_session:{state.quiz_id}"
        state_json = state.model_dump_json()
        await self.client.set(session_key, state_json, ex=ttl_seconds)

    async def get_quiz_state(self, quiz_id: uuid.UUID) -> Optional[GraphState]:
        """
        Retrieves and safely deserializes a quiz session state from Redis.
        """
        session_key = f"quiz_session:{quiz_id}"
        state_json = await self.client.get(session_key)
        if state_json:
            return GraphState.model_validate_json(state_json)
        return None

    async def update_quiz_state_atomically(
        self, quiz_id: uuid.UUID, new_data: dict
    ) -> GraphState:
        """
        Atomically updates a quiz session state using a WATCH/MULTI/EXEC transaction
        to prevent race conditions.
        """
        session_key = f"quiz_session:{quiz_id}"
        async with self.client.pipeline() as pipe:
            while True:
                try:
                    await pipe.watch(session_key)
                    state_json = await pipe.get(session_key)
                    if not state_json:
                        raise ValueError("Quiz session not found or expired.")

                    current_state = GraphState.model_validate_json(state_json)
                    
                    # Update the Pydantic model with the new data
                    updated_state = current_state.model_copy(update=new_data)
                    updated_state_json = updated_state.model_dump_json()

                    pipe.multi()
                    pipe.set(session_key, updated_state_json)
                    pipe.expire(session_key, 3600)
                    await pipe.execute()
                    
                    return updated_state
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
