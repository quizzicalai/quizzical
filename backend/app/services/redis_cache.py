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
import json
import random
import time
import uuid
from dataclasses import dataclass
from typing import Any

import redis.asyncio as redis
import structlog
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from redis.exceptions import RedisError, WatchError

from app.agent.schemas import AgentGraphStateModel
from app.agent.state import GraphState

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ensure_text(value: str | bytes | bytearray | memoryview) -> str:
    """Return a str whether Redis returned str (decode_responses=True) or bytes."""
    if isinstance(value, str):
        return value
    try:
        return bytes(value).decode("utf-8")
    except Exception:
        # Last-resort repr; shouldn't happen in normal operation.
        return str(value)


def _message_to_dict(msg: Any) -> dict[str, Any]:
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
    data: dict[str, Any] = {"type": str(mtype), "content": content}
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


def _normalize_graph_state_for_storage(state_like: GraphState | dict[str, Any] | AgentGraphStateModel) -> dict[str, Any]:
    """
    Produce a JSON-serializable dict suitable for Pydantic validation & Redis storage.
    - Messages list is normalized to plain dicts.
    - Pydantic instances are dumped.
    - No legacy field aliases; expects v0 keys (e.g., 'synopsis').
    """
    # Start with a shallow dict
    if isinstance(state_like, AgentGraphStateModel):
        out: dict[str, Any] = state_like.model_dump()
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

    # Drop transient/legacy keys the GraphState carries that are not part of
    # the canonical AgentGraphStateModel schema (which uses ``extra='forbid'``).
    # The agent's TypedDict GraphState legitimately carries ephemeral working
    # keys (``analysis``, ``topic_knowledge``, tool scratchpads, etc.) that
    # never round-trip through Redis. Without this filter, ``model_validate``
    # raises ValidationError(extra_forbidden) and ``save_quiz_state`` silently
    # logs ``redis.save_state.fail`` — the resumed session then 404s mid-quiz.
    # If a legacy ``analysis`` payload is present without ``topic_analysis``,
    # migrate it so we don't lose the planner's normalization decision.
    if isinstance(out.get("analysis"), dict) and not out.get("topic_analysis"):
        out["topic_analysis"] = out["analysis"]
    _allowed = set(AgentGraphStateModel.model_fields.keys())
    out = {k: v for k, v in out.items() if k in _allowed}

    # Final coercion for datetimes, UUIDs, etc.
    return jsonable_encoder(out)


@dataclass(frozen=True)
class QuizStatusSnapshot:
    """Hitlist #11 (2026-06-30) — the minimal slice of a stored quiz state that
    ``/quiz/status`` reads on every poll.

    A status poll only consults ~4 fields plus the single *unseen* question, yet
    the old hot path deserialised the ENTIRE graph state through
    ``AgentGraphStateModel.model_validate_json`` (full Pydantic re-validation of
    the synopsis, every character, every question, and the whole message
    history) and then ``model_dump()``-ed the whole graph on each poll — pure
    overhead repeated every 1–5s per active user. This snapshot is produced by a
    raw ``json.loads`` + plain-dict field reads (no Pydantic), preserving the
    exact downstream behaviour (the endpoint still validates ``final_result`` via
    ``FinalResult`` and the served question via ``_format_next_question``).

    All fields are best-effort and defaulted so a missing/renamed key never
    raises on the hot path. ``raw`` carries the parsed top-level dict for the
    rare branches that need another field without paying for a second parse.
    """

    trace_id: Any = None
    final_result: Any = None
    generated_questions: list[Any] = None  # type: ignore[assignment]
    quiz_history_len: int = 0
    current_confidence: Any = None
    last_served_index: Any = None
    raw: dict[str, Any] = None  # type: ignore[assignment]


def _key_session(session_id: uuid.UUID | str) -> str:
    return f"quiz_session:{session_id}"


def _key_rag(category_slug: str) -> str:
    return f"rag_cache:{category_slug}"


def _jittered_backoff(attempt: int, base: float = 0.05, cap: float = 0.5) -> float:
    """Small, bounded, jittered backoff (seconds)."""
    # linear base * attempt, then cap, then add tiny jitter
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

    async def save_quiz_state(self, state: GraphState | dict[str, Any] | AgentGraphStateModel, ttl_seconds: int = 3600) -> None:
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

    async def get_quiz_state(self, session_id: uuid.UUID) -> AgentGraphStateModel | None:
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

    async def get_quiz_status_snapshot(
        self, session_id: uuid.UUID
    ) -> QuizStatusSnapshot | None:
        """Hitlist #11 — lightweight read for the ``/quiz/status`` poll.

        Returns ``None`` on a cache MISS (the caller then rehydrates from
        Postgres exactly as before) and ``None`` on a parse/Redis fault so the
        caller's miss-path (DB rehydrate) covers it — never silently degrades.

        Unlike :meth:`get_quiz_state`, this performs a single ``json.loads`` and
        extracts ONLY the handful of fields the status response needs, skipping
        the full ``AgentGraphStateModel`` validation + ``model_dump`` of the
        whole graph state. The fields returned are byte-for-byte the same values
        the endpoint read from the validated+dumped state (they round-trip
        through the same JSON), so the response is identical.
        """
        key = _key_session(session_id)
        try:
            t0 = time.perf_counter()
            raw = await self.client.get(key)
            if raw is None:
                logger.debug("redis.get_status_snapshot.miss", key=key)
                return None
            text = _ensure_text(raw)
            data = json.loads(text)
            if not isinstance(data, dict):
                # Malformed payload — fall back to the DB rehydrate path.
                logger.debug("redis.get_status_snapshot.not_dict", key=key)
                return None

            qh = data.get("quiz_history")
            qh_len = len(qh) if isinstance(qh, list) else 0
            gq = data.get("generated_questions")
            gq_list = gq if isinstance(gq, list) else []

            logger.debug(
                "redis.get_status_snapshot.hit",
                key=key,
                bytes=len(text),
                duration_ms=round((time.perf_counter() - t0) * 1000, 1),
            )
            return QuizStatusSnapshot(
                trace_id=data.get("trace_id"),
                final_result=data.get("final_result"),
                generated_questions=gq_list,
                quiz_history_len=qh_len,
                current_confidence=data.get("current_confidence"),
                last_served_index=data.get("last_served_index"),
                raw=data,
            )
        except (RedisError, ValueError, TypeError) as e:
            # ValueError covers json.JSONDecodeError. Treat any fault as a miss
            # so the endpoint's DB-rehydrate fallback handles it (never raises).
            logger.warning(
                "redis.get_status_snapshot.fail", key=key, error=str(e)
            )
            return None

    async def update_quiz_state_atomically(
        self,
        session_id: uuid.UUID,
        new_data: dict[str, Any],
        ttl_seconds: int = 3600,
    ) -> AgentGraphStateModel | None:
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

    async def get_rag_cache(self, category_slug: str) -> str | None:
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
