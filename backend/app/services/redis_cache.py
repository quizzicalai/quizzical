"""
Redis Cache Service (Repository Pattern)

Encapsulates all interactions with Redis for:
- Quiz session state (JSON-serialized Pydantic model) with TTL
- RAG result caching (string) with TTL

Design goals:
- Async-safe: uses redis.asyncio client injected via DI
- Resilient: optimistic concurrency with bounded retry + backoff
- Observable: structured logs with key, TTL, sizes, attempts
- Compatible: preserves class/method signatures & key formats
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Optional, Union

import redis.asyncio as redis
import structlog
from pydantic import ValidationError
from redis.exceptions import RedisError, WatchError

# The agent's state is defined as a TypedDict for in-memory operations.
from app.agent.state import GraphState
# Pydantic mirror of the state for robust serialization.
from app.models.api import PydanticGraphState

logger = structlog.get_logger(__name__)


def _ensure_text(value: Union[str, bytes, bytearray]) -> str:
    """Return a str whether Redis returned str (decode_responses=True) or bytes."""
    return value if isinstance(value, str) else value.decode("utf-8")


class CacheRepository:
    """Handles all Redis cache operations."""

    def __init__(self, client: redis.Redis):
        """
        Initialize the repository with a Redis client.

        Note: The DI layer should configure the client with a shared ConnectionPool.
        Recommended pool params (set in DI, not here): decode_responses=True,
        health_check_interval, timeouts, and client-side Retry for transient network errors.
        """
        self.client = client

    # -------------------------------------------------------------------------
    # Quiz session state (JSON)
    # -------------------------------------------------------------------------

    async def save_quiz_state(
        self, state: GraphState, ttl_seconds: int = 3600
    ) -> None:
        """
        Save the state of a quiz session to Redis with a specified TTL.

        Args:
            state: The agent's GraphState dictionary.
            ttl_seconds: Time-to-live for the cache entry, in seconds.
        """
        session_id = state.get("session_id")
        if not session_id:
            logger.warning("Attempted to save state without a session_id.")
            return

        session_key = f"quiz_session:{session_id}"

        try:
            # Validate & serialize via Pydantic for safe storage
            t0 = time.perf_counter()
            state_pydantic = PydanticGraphState.model_validate(state)
            state_json = state_pydantic.model_dump_json()
            await self.client.set(session_key, state_json, ex=ttl_seconds)
            dt_ms = round((time.perf_counter() - t0) * 1000, 1)

            logger.info(
                "Saved quiz state to Redis.",
                session_id=str(session_id),
                key=session_key,
                ttl_seconds=ttl_seconds,
                json_chars=len(state_json),
                duration_ms=dt_ms,
            )
        except (ValidationError, RedisError) as e:
            logger.error(
                "Failed to save quiz state to Redis.",
                session_id=str(session_id),
                key=session_key,
                ttl_seconds=ttl_seconds,
                exc_info=True,
            )

    async def get_quiz_state(self, session_id: uuid.UUID) -> Optional[GraphState]:
        """
        Retrieve and safely deserialize a quiz session state from Redis.

        Returns:
            The agent's GraphState dictionary if found, otherwise None.
        """
        session_key = f"quiz_session:{session_id}"
        try:
            t0 = time.perf_counter()
            raw = await self.client.get(session_key)
            if not raw:
                logger.warning(
                    "Quiz state not found in Redis.",
                    session_id=str(session_id),
                    key=session_key,
                )
                return None

            text = _ensure_text(raw)
            pydantic_state = PydanticGraphState.model_validate_json(text)
            dt_ms = round((time.perf_counter() - t0) * 1000, 1)

            logger.debug(
                "Loaded quiz state from Redis.",
                session_id=str(session_id),
                key=session_key,
                json_chars=len(text),
                duration_ms=dt_ms,
            )
            return pydantic_state.model_dump()

        except (ValidationError, RedisError) as e:
            logger.error(
                "Failed to read/deserialize quiz state from Redis.",
                session_id=str(session_id),
                key=session_key,
                exc_info=True,
            )
            return None

    async def update_quiz_state_atomically(
        self, session_id: uuid.UUID, new_data: dict
    ) -> Optional[GraphState]:
        """
        Atomically update a quiz session state using a WATCH/MULTI/EXEC transaction
        to prevent race conditions.

        Args:
            session_id: The unique identifier for the quiz session.
            new_data: A dictionary of new data to merge into the state.

        Returns:
            The updated GraphState dictionary, or None if the session expired or
            the update ultimately failed after retries.
        """
        session_key = f"quiz_session:{session_id}"
        max_retries = 8  # bounded to avoid hot spinning under contention
        attempt = 0

        async with self.client.pipeline() as pipe:
            while attempt < max_retries:
                try:
                    attempt += 1
                    await pipe.watch(session_key)

                    # Read current value under WATCH
                    raw = await pipe.get(session_key)
                    if not raw:
                        logger.error(
                            "Quiz session not found or expired during atomic update.",
                            session_id=str(session_id),
                            key=session_key,
                            attempt=attempt,
                        )
                        await pipe.unwatch()
                        return None

                    state_json = _ensure_text(raw)
                    current_pydantic = PydanticGraphState.model_validate_json(state_json)
                    current_state = current_pydantic.model_dump()

                    # Merge in new data and re-validate
                    current_state.update(new_data)
                    updated_pydantic = PydanticGraphState.model_validate(current_state)
                    updated_json = updated_pydantic.model_dump_json()

                    # Start the transaction and write with TTL in a single command
                    pipe.multi()
                    pipe.set(session_key, updated_json, ex=3600)  # keep existing TTL policy
                    await pipe.execute()

                    logger.info(
                        "Atomically updated quiz state in Redis.",
                        session_id=str(session_id),
                        key=session_key,
                        attempts=attempt,
                        json_chars=len(updated_json),
                    )
                    return updated_pydantic.model_dump()

                except WatchError:
                    # Another writer changed the key; back off and retry.
                    backoff_s = 0.05 * attempt  # linear backoff; small and simple
                    logger.warning(
                        "WatchError during atomic update; retrying.",
                        session_id=str(session_id),
                        key=session_key,
                        attempt=attempt,
                        backoff_seconds=backoff_s,
                    )
                    try:
                        await pipe.unwatch()
                    except RedisError:
                        # If unwatch fails, reset the pipeline state.
                        try:
                            pipe.reset()
                        except Exception:
                            pass
                    await asyncio.sleep(backoff_s)
                    continue
                except (ValidationError, RedisError) as e:
                    logger.error(
                        "Atomic update failed due to validation/Redis error.",
                        session_id=str(session_id),
                        key=session_key,
                        attempt=attempt,
                        exc_info=True,
                    )
                    try:
                        await pipe.unwatch()
                    except Exception:
                        try:
                            pipe.reset()
                        except Exception:
                            pass
                    return None
                finally:
                    # Ensure pipeline is clean before the next attempt/exit
                    try:
                        pipe.reset()
                    except Exception:
                        pass

        logger.warning(
            "Atomic update aborted after max retries.",
            session_id=str(session_id),
            key=session_key,
            attempts=attempt,
        )
        return None

    # -------------------------------------------------------------------------
    # RAG cache (string)
    # -------------------------------------------------------------------------

    async def get_rag_cache(self, category_slug: str) -> Optional[str]:
        """
        Retrieve a cached RAG result (string) for the given category.

        Args:
            category_slug: The URL-friendly slug for the category.

        Returns:
            The cached string if present, otherwise None.
        """
        cache_key = f"rag_cache:{category_slug}"
        try:
            raw = await self.client.get(cache_key)
            if raw is None:
                logger.debug("RAG cache miss.", key=cache_key)
                return None
            text = _ensure_text(raw)
            logger.debug("RAG cache hit.", key=cache_key, bytes_or_chars=len(text))
            return text
        except RedisError:
            logger.error("Failed to read RAG cache from Redis.", key=cache_key, exc_info=True)
            return None

    async def set_rag_cache(
        self, category_slug: str, rag_result: str, ttl_seconds: int = 86400
    ) -> None:
        """
        Cache a RAG result with a specified TTL.

        Args:
            category_slug: The URL-friendly slug for the category.
            rag_result: The string content of the RAG result to cache.
            ttl_seconds: The time-to-live for the cache entry (defaults to 24 hours).
        """
        cache_key = f"rag_cache:{category_slug}"
        try:
            await self.client.set(cache_key, rag_result, ex=ttl_seconds)
            logger.info(
                "RAG cache stored.",
                key=cache_key,
                ttl_seconds=ttl_seconds,
                bytes_or_chars=len(rag_result),
            )
        except RedisError:
            logger.error("Failed to store RAG cache in Redis.", key=cache_key, exc_info=True)
