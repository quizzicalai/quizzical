# backend/app/services/redis_cache.py
"""
Redis Cache Service (Repository Pattern)

Responsibilities
- Quiz session state cache:
  * Validate against AgentGraphStateModel
  * JSON serialize; store with TTL
  * Atomic updates via WATCH/MULTI/EXEC + bounded retry
- RAG cache: simple string values with TTL

V0 alignment
- Uses the strict AgentGraphStateModel (synopsis, UUID session_id, typed history).
- Normalizes LangChain-like messages to plain dicts before storage.
- No backward compatibility shims for legacy keys.

Operational notes
- A redis.asyncio.Redis client must be injected (prefer decode_responses=True).
- Connection pool, timeouts, and client-side retry are configured in DI.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Dict, Optional, Union

import redis.asyncio as redis
import structlog
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from redis.exceptions import RedisError, WatchError

from app.agent.state import GraphState
from app.agent.schemas import AgentGraphStateModel

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_text(value: Union[str, bytes, bytearray, memoryview]) -> str:
    """Return a str whether Redis returned str (decode_responses=True) or bytes."""
    if isinstance(value, str):
        return value
    try:
        return bytes(value).decode("utf-8")
    except Exception:
        # Last-resort repr; shouldn't happen in normal operation.
        return str(value)


def _message_to_dict(msg: Any) -> Dict[str, Any]:
    """
    Best-effort conversion of LangChain BaseMessage-like objects into plain dicts
    without importing LangChain types.
    """
    if isinstance(msg, dict):
        return msg

    content = getattr(msg, "content", None)
    mtype = getattr(msg, "type", None)
    if not mtype:
        cls = msg.__class__.__name__.lower()
        if "ai" in cls:
            mtype = "ai"
        elif "human" in cls:
            mtype = "human"
        elif "system" in cls:
            mtype = "system"
        else:
            mtype = cls

    name = getattr(msg, "name", None)
    additional = getattr(msg, "additional_kwargs", None)
    data: Dict[str, Any] = {"type": str(mtype), "content": content}
    if name:
        data["name"] = name
    if isinstance(additional, dict) and additional:
        data["additional_kwargs"] = additional
    return data


def _to_plain(obj: Any) -> Any:
    """Return a plain Python object for Pydantic-like inputs; pass dicts through."""
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            return obj
    return obj


def _normalize_graph_state_for_storage(state_like: Union[GraphState, Dict[str, Any], AgentGraphStateModel]) -> Dict[str, Any]:
    """
    Produce a JSON-serializable dict suitable for Pydantic validation & Redis storage.
    - Messages list is normalized to plain dicts.
    - Pydantic instances are dumped.
    - No legacy field aliases; expects v0 keys (e.g., 'synopsis').
    """
    # Start with a shallow dict
    if isinstance(state_like, AgentGraphStateModel):
        out: Dict[str, Any] = state_like.model_dump()
    elif isinstance(state_like, dict):
        out = dict(state_like)
    else:
        # GraphState is a TypedDict, so dict() is fine
        out = dict(state_like)

    # Normalize messages
    msgs = out.get("messages")
    if isinstance(msgs, list):
        out["messages"] = [_message_to_dict(m) for m in msgs]

    # Dump known model-ish fields into plain dicts
    if out.get("synopsis") is not None:
        out["synopsis"] = _to_plain(out.get("synopsis"))

    if isinstance(out.get("generated_characters"), list):
        out["generated_characters"] = [_to_plain(c) for c in out.get("generated_characters") or []]

    if isinstance(out.get("generated_questions"), list):
        out["generated_questions"] = [_to_plain(q) for q in out.get("generated_questions") or []]

    if isinstance(out.get("quiz_history"), list):
        out["quiz_history"] = [_to_plain(h) for h in out.get("quiz_history") or []]

    # Final coercion for datetimes, UUIDs, etc.
    return jsonable_encoder(out)


def _key_session(session_id: Union[uuid.UUID, str]) -> str:
    return f"quiz_session:{session_id}"


def _key_rag(category_slug: str) -> str:
    return f"rag_cache:{category_slug}"


def _jittered_backoff(attempt: int, base: float = 0.05, cap: float = 0.5) -> float:
    """Small, bounded, jittered backoff (seconds)."""
    # linear base * attempt, then cap, then add tiny jitter
    import random
    d = min(cap, base * max(1, attempt))
    return d + random.random() * 0.01


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------

class CacheRepository:
    """Handles all Redis cache operations."""

    def __init__(self, client: redis.Redis):
        """
        Initialize the repository with a Redis client.

        DI should configure:
          - decode_responses=True (preferred)
          - timeouts, health_check_interval
          - client-side Retry for transient errors
        """
        self.client = client

    # ---------------------------------------------------------------------
    # Quiz session state (JSON)
    # ---------------------------------------------------------------------

    async def save_quiz_state(self, state: Union[GraphState, Dict[str, Any], AgentGraphStateModel], ttl_seconds: int = 3600) -> None:
        """
        Save a quiz session state (validated) to Redis with TTL.
        Expects v0 state (e.g., 'synopsis' not 'category_synopsis').
        """
        session_id = (state.get("session_id") if isinstance(state, dict) else getattr(state, "session_id", None))  # type: ignore[index]
        if not session_id:
            logger.warning("redis.save_state.missing_session_id")
            return

        key = _key_session(session_id)
        try:
            t0 = time.perf_counter()
            normalized = _normalize_graph_state_for_storage(state)
            # Validate (will coerce UUID from str when needed)
            state_pyd = AgentGraphStateModel.model_validate(normalized)
            payload = state_pyd.model_dump_json()
            await self.client.set(key, payload, ex=ttl_seconds)

            logger.info(
                "redis.save_state.ok",
                session_id=str(state_pyd.session_id),
                key=key,
                ttl_seconds=ttl_seconds,
                bytes=len(payload),
                duration_ms=round((time.perf_counter() - t0) * 1000, 1),
            )
        except (ValidationError, RedisError) as e:
            logger.error(
                "redis.save_state.fail",
                session_id=str(session_id),
                key=key,
                ttl_seconds=ttl_seconds,
                error=str(e),
                exc_info=True,
            )

    async def get_quiz_state(self, session_id: uuid.UUID) -> Optional[AgentGraphStateModel]:
        """Retrieve and deserialize a quiz session state."""
        key = _key_session(session_id)
        try:
            t0 = time.perf_counter()
            raw = await self.client.get(key)
            if raw is None:
                logger.debug("redis.get_state.miss", key=key)
                return None

            text = _ensure_text(raw)
            model = AgentGraphStateModel.model_validate_json(text)

            logger.debug(
                "redis.get_state.hit",
                session_id=str(model.session_id),
                key=key,
                bytes=len(text),
                duration_ms=round((time.perf_counter() - t0) * 1000, 1),
            )
            return model
        except (ValidationError, RedisError) as e:
            logger.error("redis.get_state.fail", key=key, error=str(e), exc_info=True)
            return None

    async def update_quiz_state_atomically(
        self,
        session_id: uuid.UUID,
        new_data: Dict[str, Any],
        ttl_seconds: int = 3600,
    ) -> Optional[AgentGraphStateModel]:
        """
        Atomically merge `new_data` into the stored state with optimistic concurrency.
        Shallow merge (dict.update); callers should pass fully formed fields for lists.
        """
        key = _key_session(session_id)
        max_retries = 8
        attempt = 0

        async with self.client.pipeline() as pipe:
            while attempt < max_retries:
                attempt += 1
                try:
                    await pipe.watch(key)
                    raw = await pipe.get(key)
                    if raw is None:
                        logger.warning("redis.state_update.missing", key=key, attempt=attempt)
                        await pipe.unwatch()
                        return None

                    current_json = _ensure_text(raw)
                    current_model = AgentGraphStateModel.model_validate_json(current_json)
                    current_state = current_model.model_dump()

                    # Shallow merge; caller controls semantics for list fields
                    current_state.update(new_data)

                    normalized = _normalize_graph_state_for_storage(current_state)
                    updated_model = AgentGraphStateModel.model_validate(normalized)
                    updated_json = updated_model.model_dump_json()

                    pipe.multi()
                    pipe.set(key, updated_json, ex=ttl_seconds)
                    await pipe.execute()

                    logger.debug(
                        "redis.state_update.ok",
                        session_id=str(updated_model.session_id),
                        key=key,
                        attempt=attempt,
                        ttl_seconds=ttl_seconds,
                        bytes=len(updated_json),
                    )
                    return updated_model

                except WatchError:
                    backoff = _jittered_backoff(attempt)
                    logger.debug("redis.state_update.watch_conflict", key=key, attempt=attempt, sleep_s=round(backoff, 3))
                    try:
                        await pipe.unwatch()
                    except Exception:
                        try:
                            pipe.reset()
                        except Exception:
                            pass
                    await asyncio.sleep(backoff)
                    continue
                except (ValidationError, RedisError) as e:
                    logger.error("redis.state_update.fail", key=key, attempt=attempt, error=str(e), exc_info=True)
                    try:
                        await pipe.unwatch()
                    except Exception:
                        try:
                            pipe.reset()
                        except Exception:
                            pass
                    return None
                finally:
                    try:
                        pipe.reset()
                    except Exception:
                        pass

        logger.warning("redis.state_update.gave_up", key=key, attempts=attempt)
        return None

    # ---------------------------------------------------------------------
    # RAG cache (string)
    # ---------------------------------------------------------------------

    async def get_rag_cache(self, category_slug: str) -> Optional[str]:
        """Return cached RAG string for a category slug, or None."""
        key = _key_rag(category_slug)
        try:
            raw = await self.client.get(key)
            if raw is None:
                logger.debug("redis.rag.miss", key=key)
                return None
            text = _ensure_text(raw)
            logger.debug("redis.rag.hit", key=key, bytes=len(text))
            return text
        except RedisError as e:
            logger.error("redis.rag.get.fail", key=key, error=str(e), exc_info=True)
            return None

    async def set_rag_cache(self, category_slug: str, rag_result: str, ttl_seconds: int = 86_400) -> None:
        """Store RAG string with TTL (default 24h)."""
        key = _key_rag(category_slug)
        try:
            await self.client.set(key, rag_result, ex=ttl_seconds)
            logger.info("redis.rag.set.ok", key=key, ttl_seconds=ttl_seconds, bytes=len(rag_result))
        except RedisError as e:
            logger.error("redis.rag.set.fail", key=key, error=str(e), exc_info=True)
