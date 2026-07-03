# backend/app/api/endpoints/quiz.py
"""
API Endpoints for Quiz Interaction (gated questions flow)

Flow:
- /quiz/start: creates synopsis and tries to stream characters within a time budget.
- /quiz/proceed: sets ready_for_questions=True and continues the agent to generate
  baseline questions in the background.
- /quiz/next: submits an answer and continues the agent in the background.
- /quiz/status: returns the next *unseen* question or final result.

Notes:
- Time budgets read from settings with safe fallbacks (30s).
- Uses a duck-typed compiled graph instance (has ainvoke/astream/aget_state).
- Minimal, idempotent persistence added to /quiz/start to verify DB writes,
  implemented via the new repositories (no legacy columns).
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
import time
import traceback
import uuid
from typing import Annotated, Any

import structlog
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    status,
)
from fastapi.encoders import jsonable_encoder  # NEW
from langchain_core.messages import HumanMessage
from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.schemas import (
    AgentGraphStateModel,
    QuizQuestion,  # noqa: F401 (type clarity)
)
from app.agent.state import GraphState
from app.api.dependencies import (
    get_db_session,
    get_precompute_lookup,
    get_redis_client,
    verify_turnstile,
)
from app.core.coercion import coerce_to_dict
from app.core.config import settings
from app.core.error_codes import (
    QF_AGENT_FAILED,
    QF_AGENT_NO_SYNOPSIS,
    QF_AGENT_TIMEOUT,
    QF_AGENT_UNAVAILABLE,
    QF_COST_CEILING,
    QF_LLM_PROVIDER_DOWN,
    QF_LLM_RATE_LIMITED,
    QF_LLM_RESPONSE_TOO_LARGE,
    QF_MALFORMED_QUESTION,
    QF_QUIZ_BAD_ANSWER,
    QF_QUIZ_STALE_ANSWER,
    QF_QUIZ_START_RATE_LIMITED,
    QF_REINTERPRET_CAP,
    QF_SESSION_ACTION_CAP,
    QF_SESSION_BUSY,
    QF_UNKNOWN,
    get_spec,
)
from app.core.errors import NotFoundError, SessionBusyError, coded_http_exception
from app.models.api import (
    AnswerOption,
    CharacterImage,
    CharactersPayload,
    FinalResult,
    FrontendStartQuizResponse,
    NextQuestionRequest,
    ProceedRequest,
    ProcessingResponse,
    QuizMediaResponse,
    QuizStatusQuestion,
    QuizStatusResponse,
    QuizStatusResult,
    StartQuizPayload,
    StartQuizRequest,
)
from app.models.api import (
    Question as APIQuestion,
)
from app.models.api import (
    QuizQuestion as APIQuizQuestion,
)
from app.models.api import (
    Synopsis as APISynopsis,
)
from app.models.db import character_session_map
from app.security.rate_limit import RateLimiter, _client_ip
from app.services import image_pipeline as _image_pipeline

# NEW: use repositories & association table for persistence
from app.services.database import (
    CharacterRepository,
    QuizJobRepository,
    SessionQuestionsRepository,
    SessionRepository,
)
from app.services.redis_cache import CacheRepository

router = APIRouter()
logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Local utilities
# ---------------------------------------------------------------------------

def _hashed_client_ip(request: Any) -> str:
    """Hitlist #6 (2026-06-30) — HMAC-hash the client IP for LOG lines so we keep
    the hashed-IP privacy posture (we never persist/log a raw IP). Reuses the
    same flag-HMAC ``hash_ip`` util the content-flag path uses. Never raises; on
    any error returns a stable sentinel so logging is never the thing that breaks
    a request."""
    try:
        from app.services.precompute.flag_aggregator import hash_ip
        return hash_ip(_client_ip(request), secret=settings.FLAG_HMAC_SECRET)
    except Exception:
        return "iphash_error"


def _is_local_env() -> bool:
    try:
        return (settings.app.environment or "local").lower() in {"local", "dev", "development"}
    except Exception:
        return False


def _safe_len(obj) -> int | None:
    try:
        return len(obj)  # type: ignore[arg-type]
    except Exception:
        return None


def _exc_details() -> dict:
    et, ev, tb = sys.exc_info()
    return {
        "error_type": et.__name__ if et else "Unknown",
        "error_message": str(ev) if ev else "",
        "traceback": traceback.format_exc() if tb else "",
    }


def _is_transient_agent_error(exc: BaseException) -> bool:
    """Hitlist #8 (2026-06-30) — classify an in-process agent-run exception as a
    PRECISELY-RETRIABLE transient (worth a recovery re-run) vs a DETERMINISTIC
    failure (a schema/validation/programming bug a re-run cannot fix).

    Transient (leave the job recoverable): the LLM transient class
    (RateLimitError/Timeout/5xx/connection — reused from ``llm_service`` so the
    classification stays in lockstep with the retry layer), the FAL transient
    class (network/server/rate-limit — reused from ``image_service``), and the
    bare network/timeout families that a brief DB/Redis/FAL blip surfaces.

    Deterministic (fail fast, NO retry): ``StructuredOutputError`` (the model
    returned unparseable/invalid JSON), ``ValidationError`` (the agent built a
    bad artifact), and any other ``Exception`` — e.g. a programming bug. We
    deliberately default to NON-transient so a code bug fails fast instead of
    being re-run ``max_attempts`` times (and re-spending paid calls each time).
    """
    # Reuse the exact transient predicates the retry layers use so this never
    # drifts from what the LLM/FAL retry code considers transient.
    from app.services.image_service import _is_fal_transient
    from app.services.llm_service import StructuredOutputError, _is_llm_transient

    # Deterministic: a parse/validation failure is NOT fixable by re-running.
    if isinstance(exc, (StructuredOutputError, ValidationError)):
        return False
    if _is_llm_transient(exc) or _is_fal_transient(exc):
        return True
    # Bare network/timeout families (DB/Redis/FAL hiccup) that the narrower
    # predicates above may not catch directly.
    if isinstance(exc, (asyncio.TimeoutError, ConnectionError, OSError, TimeoutError)):
        return True
    return False


def _qf_code_for_transient(exc: BaseException) -> str | None:
    """Hitlist #4 (2026-06-30) — map a TRANSIENT LLM/agent failure to its PRECISE
    whimsical ``QF-`` code for triage, instead of collapsing every failure to
    QF-UNKNOWN. Returns None for a non-transient / unclassifiable error so the
    caller keeps its existing catch-all code (no behaviour change there).

    Reuses the EXACT classifiers the retry layers use (``_is_llm_transient`` /
    the litellm exception families / ``LLMResponseTooLargeError``) so this never
    drifts from what the system already considers transient:

      * rate-limit (429)          -> QF-LLM-RATE-LIMITED
      * timeout                   -> QF-AGENT-TIMEOUT
      * oversized response        -> QF-LLM-RESPONSE-TOO-LARGE
      * other provider down/5xx   -> QF-LLM-PROVIDER-DOWN
    """
    import asyncio as _asyncio

    import litellm

    from app.services.llm_service import (
        LLMResponseTooLargeError,
        _is_llm_transient,
    )

    # Oversized provider response (a buggy/compromised provider). Specific code.
    if isinstance(exc, LLMResponseTooLargeError):
        return QF_LLM_RESPONSE_TOO_LARGE

    # Rate limit (429) — checked before the generic transient bucket.
    rate_limit_cls = getattr(litellm, "RateLimitError", None)
    if isinstance(rate_limit_cls, type) and isinstance(exc, rate_limit_cls):
        return QF_LLM_RATE_LIMITED

    # Timeout (litellm.Timeout or asyncio.TimeoutError).
    timeout_classes = tuple(
        c for c in (getattr(litellm, "Timeout", None), _asyncio.TimeoutError)
        if isinstance(c, type)
    )
    if timeout_classes and isinstance(exc, timeout_classes):
        return QF_AGENT_TIMEOUT

    # Any other LLM transient (connection / 5xx / service-unavailable) -> provider down.
    if _is_llm_transient(exc):
        return QF_LLM_PROVIDER_DOWN

    return None


def _agent_recovery_max_attempts() -> int:
    try:
        return int(settings.security.agent_recovery.max_attempts)
    except Exception:
        return 3


def _as_payload_dict(obj: Any, variant: str) -> dict[str, Any]:
    """Normalize for StartQuizPayload discriminated union."""
    base = coerce_to_dict(obj) if obj is None or isinstance(obj, dict) or hasattr(
        obj, "model_dump"
    ) else None
    if base is None:
        validated_model = APISynopsis if variant == "synopsis" else APIQuizQuestion
        base = validated_model.model_validate(obj).model_dump()
    base["type"] = variant
    return base


def _character_to_dict(obj: Any) -> dict[str, Any]:
    if obj is None or isinstance(obj, dict) or hasattr(obj, "model_dump"):
        return coerce_to_dict(obj)
    return {
        "name": getattr(obj, "name", ""),
        "short_description": getattr(obj, "short_description", ""),
        "profile_text": getattr(obj, "profile_text", ""),
        "image_url": getattr(obj, "image_url", None),
    }


def _drop_invalid_characters_from_state(
    state: GraphState, *, quiz_id: uuid.UUID
) -> None:
    """
    AC-START-11 / AC-START-12 — drop characters whose ``profile_text`` is
    empty or whitespace-only from ``state["generated_characters"]`` before
    the snapshot is persisted, before the response is built, and before image
    background tasks are scheduled.

    Background: when a per-character LLM call fails (e.g. a Gemini ``503
    ServiceUnavailable`` after retry exhaustion) the agent emits a
    ``CharacterProfile`` with ``profile_text=""``. The Postgres CHECK
    constraint ``characters_profile_text_check`` rejects empty strings, which
    would roll back the entire ``session_history`` insert and silently break
    the media pipeline (``/quiz/{id}/media`` would forever return an empty
    snapshot because the row that image tasks ``UPDATE`` never exists).
    Filtering at the API boundary preserves AC-START-8 and AC-MEDIA-2/3/4.
    """
    chars = state.get("generated_characters") or []
    if not chars:
        return

    kept: list[Any] = []
    dropped_names: list[str] = []
    for c in chars:
        cd = _character_to_dict(c)
        profile_text = (cd.get("profile_text") or "").strip()
        if not profile_text:
            dropped_names.append(str(cd.get("name") or "<unnamed>"))
            continue
        kept.append(c)

    if dropped_names:
        state["generated_characters"] = kept
        logger.warning(
            "start_quiz.characters.filtered_empty",
            quiz_id=str(quiz_id),
            dropped_count=len(dropped_names),
            dropped_names=dropped_names,
            kept_count=len(kept),
        )


def _to_state_dict(obj: Any) -> dict[str, Any]:
    if obj is None or isinstance(obj, dict) or hasattr(obj, "model_dump"):
        return coerce_to_dict(obj)
    try:
        return AgentGraphStateModel.model_validate(obj).model_dump()
    except Exception:
        return dict(obj or {})


# ---------------------------------------------------------------------------
# Graph dependency
# ---------------------------------------------------------------------------

def get_agent_graph(request: Request) -> object:
    agent_graph = getattr(request.app.state, "agent_graph", None)
    if agent_graph is None:
        logger.error(
            "Agent graph not found in application state. The app may not have started correctly.",
            path="/quiz/*",
        )
        raise coded_http_exception(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent service is not available.",
            code=QF_AGENT_UNAVAILABLE,
        )
    logger.debug(
        "Agent graph loaded from app state",
        has_agent_graph=True,
        agent_graph_type=type(agent_graph).__name__,
        agent_graph_id=id(agent_graph),
    )
    return agent_graph


# ---------------------------------------------------------------------------
# Minimal persistence helpers (used only in /quiz/start)
# ---------------------------------------------------------------------------

def _serialize_synopsis(obj: Any) -> dict[str, Any]:
    if obj is None or isinstance(obj, dict) or hasattr(obj, "model_dump"):
        return coerce_to_dict(obj)
    return {
        "title": getattr(obj, "title", None) or "",
        "summary": getattr(obj, "summary", None) or "",
    }


def _bootstrap_transcript(category: str, synopsis_dict: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Minimal transcript to seed the DB row:
      - user: category
      - assistant: synopsis payload
    """
    return [
        {"role": "user", "content": category},
        {"role": "assistant", "content": {"type": "synopsis", **synopsis_dict}},
    ]

async def _insert_characters_if_absent(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    characters: list[Any],
) -> None:
    """
    Upsert characters (unique by name) and link them to this session via M:N table.
    Does NOT touch session transcript / synopsis.
    """
    if not characters:
        return

    char_repo = CharacterRepository(db)

    # Upsert characters and collect their IDs
    ids: list[uuid.UUID] = []
    for c in characters:
        cd = _character_to_dict(c)
        name = (cd.get("name") or "").strip()
        if not name:
            continue
        short_description = cd.get("short_description") or ""
        profile_text = cd.get("profile_text") or ""
        char = await char_repo.upsert_by_name(
            name=name,
            short_description=short_description,
            profile_text=profile_text,
        )
        if char:
            ids.append(char.id)

    if not ids:
        return

    # Insert links; ignore duplicates (must EXECUTE the statement)
    link_stmt = (
        pg_insert(character_session_map)
        .values([{"character_id": cid, "session_id": session_id} for cid in ids])
        .on_conflict_do_nothing()
    )
    await db.execute(link_stmt)


async def _persist_initial_snapshot(
    db: AsyncSession,
    *,
    session_id: uuid.UUID,
    category: str,
    synopsis: Any,
    characters: list[Any],
    write_session_row: bool = True,
    agent_plan: dict[str, Any] | None = None,
) -> None:
    """
    One transaction:
      - Upsert session row (synopsis + optional agent_plan + optional character_set snapshot)
      - Upsert/link character associations (idempotent)
    """
    repo = SessionRepository(db)
    syn = _serialize_synopsis(synopsis)
    transcript = _bootstrap_transcript(category, syn)
    character_set = [_character_to_dict(c) for c in (characters or [])] or None

    await repo.upsert_session_after_synopsis(
        session_id=session_id,
        category=category,
        synopsis_dict=syn,
        transcript=transcript,
        characters_payload=None,     # association table handled below
        completed=False,
        agent_plan=agent_plan if write_session_row else None,  # don’t overwrite with None later
        character_set=character_set,  # set/refresh whenever we see characters
    )
    await _insert_characters_if_absent(db, session_id=session_id, characters=characters)
    await db.commit()


# ---------------------------------------------------------------------------
# Background runner helpers (Extracted to fix C901)
# ---------------------------------------------------------------------------

# Fields the agent owns and is allowed to write back to the cached state.
# These are produced by the LangGraph run; merging only these via the atomic
# WATCH/MULTI path preserves any request-owned fields a concurrent
# /quiz/next or /quiz/status updated while the agent was running.
_AGENT_OWNED_STATE_FIELDS: tuple[str, ...] = (
    "generated_questions",
    "baseline_count",
    "baseline_ready",
    "current_confidence",
    "final_result",
    "agent_plan",
    "should_finalize",
    "synopsis",
    "generated_characters",
    "analysis",
    "topic_analysis",
    "outcome_kind",
    "creativity_mode",
    "ideal_archetypes",
)
# Deliberately omitted: ``is_error``/``error_message``/``error_count``/
# ``rag_context`` are observability-only working fields the agent mutates but
# that /status (and the rest of the read path) never reads for control logic,
# so there is no need to merge them back and risk widening the write surface.

# Request-owned fields the agent's final save must NEVER clobber. A delayed
# /quiz/next (records an answer into ``quiz_history``/``messages``) or a
# /quiz/status (advances ``last_served_index``) may land mid-run; a full-state
# SET here would silently drop those concurrent atomic merges (audit P1).
_REQUEST_OWNED_STATE_FIELDS: frozenset[str] = frozenset(
    {"quiz_history", "messages", "last_served_index", "ready_for_questions"}
)


async def _save_final_state_to_cache(cache_repo: CacheRepository, session_id: str, state: GraphState) -> None:
    """Persist ONLY the agent-owned fields of the final state via the atomic
    merge path — never a full-state SET.

    Background (audit P1, reliability/quiz-flow): the background agent runs for
    several seconds. A ``save_quiz_state`` (full Redis SET of the whole
    snapshot) at the end would overwrite any concurrent atomic merges that
    ``/quiz/next`` (``quiz_history``/``messages``) and ``/quiz/status``
    (``last_served_index``) made while the agent was working — dropping a
    recorded answer or reverting the served pointer. We therefore merge only
    the fields the agent produced, explicitly excluding the request-owned
    fields, through ``update_quiz_state_atomically`` (WATCH/MULTI).
    """
    if not isinstance(state, dict):
        try:
            state = dict(state)  # type: ignore[arg-type]
        except Exception:
            logger.error(
                "Failed to save final agent state to cache: non-mapping state",
                quiz_id=session_id,
            )
            return

    session_uuid = state.get("session_id")
    if not isinstance(session_uuid, uuid.UUID):
        try:
            session_uuid = uuid.UUID(str(session_uuid))
        except Exception:
            logger.error(
                "Failed to save final agent state to cache: bad session_id",
                quiz_id=session_id,
            )
            return

    # Build the field-scoped merge: only agent-owned fields that are present in
    # the final state, with request-owned fields hard-excluded as a guardrail.
    merge_fields: dict[str, Any] = {
        k: state[k]
        for k in _AGENT_OWNED_STATE_FIELDS
        if k in state and k not in _REQUEST_OWNED_STATE_FIELDS
    }

    if not merge_fields:
        logger.info(
            "Final agent state has no agent-owned fields to merge; skipping save",
            quiz_id=session_id,
        )
        return

    try:
        t_save = time.perf_counter()
        merged = await cache_repo.update_quiz_state_atomically(session_uuid, merge_fields)
        save_ms = round((time.perf_counter() - t_save) * 1000, 1)
        if merged is None:
            # The atomic merge found nothing to merge into. Two cases, both
            # handled by falling back to a full-state SET:
            #   1. Missing key — Redis evicted the live state, or the
            #      crash-recovery path rebuilt state from Postgres but never
            #      re-primed Redis. The full save recreates the key.
            #   2. WATCH conflict exhausted — the key is present but stale.
            #      Without the full SET the stale snapshot (no final_result)
            #      would persist until the 3600s TTL, wedging /status on
            #      "processing" (it never cache-misses, so it never rehydrates
            #      the finished result from the DB) — a regression vs. the old
            #      unconditional full SET.
            # This is safe POST-FINALIZATION: once the agent has produced its
            # terminal state, /next and /status are no longer racing it, so the
            # very reason we needed the field-scoped merge no longer applies.
            # Best-effort: save_quiz_state never raises out of here either.
            logger.warning(
                "Final agent state merge returned no state; falling back to full save",
                quiz_id=session_id,
                merged_fields=sorted(merge_fields.keys()),
            )
            await cache_repo.save_quiz_state(state)
            logger.info(
                "Final agent state saved to cache (full SET fallback)",
                quiz_id=session_id,
                save_duration_ms=round((time.perf_counter() - t_save) * 1000, 1),
            )
            return
        logger.info(
            "Final agent state merged to cache (field-scoped, atomic)",
            quiz_id=session_id,
            save_duration_ms=save_ms,
            merged_fields=sorted(merge_fields.keys()),
        )
    except Exception as e:
        logger.error(
            "Failed to save final agent state to cache",
            quiz_id=session_id,
            error=str(e),
            **_exc_details(),
            exc_info=True,
        )


async def _persist_baseline_questions(
    session_id: uuid.UUID, session_id_str: str, state: GraphState
) -> None:
    """Persist baseline questions blob to DB (idempotent)."""
    if not (isinstance(state, dict) and state.get("baseline_ready")):
        return

    baseline_count = int(state.get("baseline_count") or 0)
    if baseline_count <= 0:
        return

    try:
        agen = get_db_session()  # borrow an AsyncSession from the dependency
        db = await agen.__anext__()
        try:
            sq_repo = SessionQuestionsRepository(db)
            # Skip if baseline already exists
            already = await sq_repo.baseline_exists(session_id)
            if not already:
                baseline_blob = {
                    "questions": (state.get("generated_questions") or [])[:baseline_count]
                }
                props = {"baseline_count": baseline_count, "source": "agent_graph_v1"}
                await sq_repo.upsert_baseline(
                    session_id=session_id,
                    baseline_blob=baseline_blob,
                    properties=props,
                )
                await db.commit()
                logger.info(
                    "Baseline questions persisted",
                    quiz_id=session_id_str,
                    baseline_count=baseline_count,
                )
            else:
                logger.debug(
                    "Baseline questions already persisted; skipping",
                    quiz_id=session_id_str,
                )
        finally:
            await agen.aclose()
    except Exception as e:
        logger.error(
            "Failed to persist baseline questions",
            quiz_id=session_id_str,
            error=str(e),
            **_exc_details(),
            exc_info=True,
        )


async def _persist_adaptive_and_final(
    session_id: uuid.UUID, session_id_str: str, state: GraphState
) -> None:
    """Persist adaptive questions blob, final result, and QA history."""
    try:
        agen = get_db_session()
        db = await agen.__anext__()
        try:
            sq_repo = SessionQuestionsRepository(db)
            sess_repo = SessionRepository(db)

            # 1) Adaptive questions snapshot (idempotent overwrite)
            if isinstance(state, dict) and state.get("baseline_ready"):
                baseline_count = int(state.get("baseline_count") or 0)
                all_qs = list(state.get("generated_questions") or [])
                adaptive_qs = all_qs[baseline_count:] if baseline_count >= 0 else []

                if adaptive_qs:
                    adaptive_blob = {"questions": adaptive_qs}
                    props = {
                        "baseline_count": baseline_count,
                        "adaptive_count": len(adaptive_qs),
                        "total_count": len(all_qs),
                        "source": "agent_graph_v1",
                    }
                    await sq_repo.upsert_adaptive(
                        session_id=session_id,
                        adaptive_blob=adaptive_blob,
                        properties=props,
                    )

            # 2) Final result + QA history (atomic mark-completed)
            if isinstance(state, dict) and state.get("final_result"):
                fr_payload = jsonable_encoder(state.get("final_result"))
                qa_hist_payload = jsonable_encoder(list(state.get("quiz_history") or []))
                await sess_repo.mark_completed(
                    session_id=session_id,
                    final_result=fr_payload,
                    qa_history=qa_hist_payload,
                )
            await db.commit()

            # 3) Result image (best-effort; runs inside this background task)
            try:
                if isinstance(state, dict) and state.get("final_result"):
                    await _image_pipeline.generate_result_image(
                        session_id=session_id,
                        result=state.get("final_result"),
                        category=str(state.get("category") or ""),
                        character_set=list(state.get("generated_characters") or []),
                        analysis=(
                            state.get("analysis")
                            or state.get("topic_analysis")
                            or {}
                        ),
                    )
            except Exception as ie:
                logger.info("image.result.schedule.fail", quiz_id=session_id_str, error=str(ie))
            logger.info("DB snapshot persisted (adaptive/final/qa)", quiz_id=session_id_str)
        finally:
            await agen.aclose()
    except Exception as e:
        logger.error(
            "Failed to persist adaptive/final/qa",
            quiz_id=session_id_str,
            error=str(e),
            **_exc_details(),
            exc_info=True,
        )


# ---------------------------------------------------------------------------
# Background runner
# ---------------------------------------------------------------------------

async def _quiz_job_update(quiz_id: uuid.UUID, op: str, *, error: str | None = None) -> None:
    """Best-effort quiz_jobs lifecycle write
    (running/succeeded/failed/retryable/heartbeat). A job-table fault must NEVER
    affect the agent run, so all errors are swallowed.

    ``retryable`` (Hitlist #8): a transient in-process failure that should be
    re-run by the recovery sweeper rather than marked terminally failed. It
    keeps status 'running' but stales the heartbeat so the next claim_stale
    picks it up immediately (bounded by max_attempts)."""
    try:
        agen = get_db_session()
        db = await agen.__anext__()
        try:
            repo = QuizJobRepository(db)
            if op == "running":
                await repo.mark_running(quiz_id)
            elif op == "succeeded":
                await repo.mark_succeeded(quiz_id)
            elif op == "heartbeat":
                await repo.heartbeat(quiz_id)
            elif op == "retryable":
                await repo.mark_retryable(quiz_id, error)
            else:
                await repo.mark_failed(quiz_id, error)
            await db.commit()
        finally:
            await agen.aclose()
    except Exception:
        logger.debug("quiz_job.update.fail", quiz_id=str(quiz_id), op=op)


async def _finalize_durable_job(
    quiz_id: uuid.UUID, *, job_ok: bool, job_exc: BaseException | None, job_error: str | None
) -> None:
    """Hitlist #8 (2026-06-30) — close out the durable job, classifying a
    failure BEFORE the terminal mark.

      - success                    -> succeeded
      - transient AND attempts<max -> 'retryable' (status stays 'running' with a
        STALE heartbeat) so the next claim_stale re-runs it, bounded by
        max_attempts + fail_exhausted (no infinite retry / no double-spend).
      - deterministic (schema/validation/bug) OR attempts exhausted -> failed
        (fail fast; /status surfaces the terminal 422).

    The previous finally marked ANY in-process exception 'failed', but the
    recovery sweeper only claims status=='running' rows — so a precisely-
    retriable transient became a terminal 422 with ZERO recovery even though the
    durable system already retries CRASHES. This does NOT regress Theme-A: the
    heartbeat is cancelled by the caller before this runs; claim_stale still
    bumps attempts atomically; fail_exhausted still trips at max_attempts; the
    final_result short-circuit and synchronous pre-schedule row are untouched."""
    if job_ok:
        await _quiz_job_update(quiz_id, "succeeded")
        return
    if (
        job_exc is not None
        and _is_transient_agent_error(job_exc)
        and await _job_attempts_below_max(quiz_id)
    ):
        logger.warning(
            "Agent run failed transiently; leaving job recoverable",
            quiz_id=str(quiz_id),
            error=job_error,
            error_type=type(job_exc).__name__,
        )
        await _quiz_job_update(quiz_id, "retryable", error=job_error)
        return
    await _quiz_job_update(quiz_id, "failed", error=job_error)
    # Whimsical-error-system (2026-06-30): a TERMINAL agent failure (deterministic
    # bug or transient that exhausted its recovery budget) maps to QF-AGENT-FAILED
    # which notify_support=True. Fire the rate-limited, deduped, fail-open Resend
    # alert here from the background task (there is a running loop). This runs
    # OUTSIDE the request path so it can never affect the user's response.
    try:
        from app.core.error_codes import get_spec
        from app.services.support_notify import maybe_notify_support

        maybe_notify_support(
            get_spec(QF_AGENT_FAILED),
            trace_id=structlog.contextvars.get_contextvars().get("trace_id"),
            path="/quiz (background agent)",
            context={
                "quiz_id": str(quiz_id),
                "error_type": type(job_exc).__name__ if job_exc else "unknown",
            },
        )
    except Exception:
        logger.debug("quiz.agent_failed.notify_failed", quiz_id=str(quiz_id))


async def _job_attempts_below_max(quiz_id: uuid.UUID) -> bool:
    """Hitlist #8 — True iff this job still has recovery budget left
    (``attempts < max_attempts``). Used to decide whether a TRANSIENT in-process
    failure should be left recoverable or failed-hard (budget exhausted). The
    attempts counter was already bumped by this run's ``mark_running``, so a
    transient failure on the final allowed attempt fails hard rather than
    looping. Fail-CLOSED on any error (treat as no budget -> mark failed) so a
    job-table fault can never cause an unbounded retry loop."""
    try:
        agen = get_db_session()
        db = await agen.__anext__()
        try:
            attempts = await QuizJobRepository(db).get_attempts(quiz_id)
        finally:
            await agen.aclose()
        if attempts is None:
            return False
        return int(attempts) < _agent_recovery_max_attempts()
    except Exception:
        logger.debug("quiz_job.attempts_probe.fail", quiz_id=str(quiz_id))
        return False


async def _ensure_job_row_before_schedule(
    db_session: AsyncSession, quiz_id: uuid.UUID
) -> None:
    """Create/mark the durable ``quiz_jobs`` row SYNCHRONOUSLY (committed) before
    a background agent run is scheduled and the 202 returned (audit P1).

    Previously the row was created only inside ``run_agent_in_background`` — i.e.
    AFTER the 202. A worker killed in that window (deploy / OOM / scale-in /
    SIGTERM, the single most likely crash instant) left NO row at all, so the
    sweeper (which claims only ``status='running'``) had nothing to recover and
    the quiz was stranded in 'processing' forever. Marking the row up-front gives
    the sweeper something to claim even if the worker dies before the bg task's
    first write. Best-effort: a job-table fault must never block the user's quiz,
    so all errors are swallowed (the bg task's own mark_running is the backstop).
    """
    try:
        # reset_attempts: a NEW user-initiated run starts with a full recovery
        # budget; the bg task's own mark_running then makes it attempt 1. Without
        # the reset, attempts would accumulate across every /proceed + /next and
        # prematurely exhaust max_attempts for a crash on a later question.
        await QuizJobRepository(db_session).mark_running(quiz_id, reset_attempts=True)
        await db_session.commit()
    except Exception:
        try:
            await db_session.rollback()
        except Exception:
            pass
        logger.debug("quiz_job.preschedule.fail", quiz_id=str(quiz_id))


async def _heartbeat_loop(quiz_id: uuid.UUID, interval_s: float) -> None:
    """Emit a durable quiz_jobs heartbeat on a timer for the duration of a run.

    Without this, ``stale_after_s`` (default 180s) is a hard wall-clock deadline
    rather than a liveness signal: a legitimately slow-but-alive run (slow
    finalization + FAL result image — the FE allows up to ~5min) is misclassified
    stale and re-claimed by the recovery sweeper, double-spending paid LLM+FAL
    on a CONCURRENT re-run (audit P1). The loop refreshes ``last_heartbeat_at``
    every ``interval_s`` so an alive run is never mis-claimed. Best-effort: a
    heartbeat fault is logged-at-debug and the loop continues; the task is
    cancelled in ``run_agent_in_background``'s ``finally``.
    """
    try:
        while True:
            await asyncio.sleep(interval_s)
            await _quiz_job_update(quiz_id, "heartbeat")
    except asyncio.CancelledError:
        raise
    except Exception:
        # Never let the heartbeat loop bubble — it must not abort the run.
        logger.debug("quiz_job.heartbeat_loop.error", quiz_id=str(quiz_id))


def _heartbeat_interval_s() -> float:
    """Heartbeat cadence ≈ stale_after_s / 3 (clamped) so an alive run refreshes
    its liveness well before the staleness deadline, with margin for jitter."""
    try:
        stale_after_s = int(settings.security.agent_recovery.stale_after_s)
    except Exception:
        stale_after_s = 180
    return max(5.0, min(60.0, stale_after_s / 3.0))


async def _load_state_with_final_result(
    quiz_id: uuid.UUID, redis_client: Any
) -> GraphState | None:
    """Return the persisted state for ``quiz_id`` IF it already carries a
    ``final_result`` (Redis live state first, then the durable DB snapshot),
    else None.

    Used to short-circuit a recovery re-run of an ALREADY-FINISHED quiz (audit
    P1): the original run can finish + persist ``final_result`` but crash before
    ``mark_succeeded``, leaving the row ``running``. Re-streaming the graph would
    make fresh paid decision/finalization calls and overwrite the result the
    user already saw. Best-effort: any lookup fault returns None (caller proceeds
    with the normal re-run, still bounded by the action cap + max_attempts).
    """
    try:
        model = await CacheRepository(redis_client).get_quiz_state(quiz_id)
        if model is not None:
            state = model.model_dump()
            if state.get("final_result"):
                return state
    except Exception:
        logger.debug("agent.final_result_probe.redis_fail", quiz_id=str(quiz_id))
    try:
        factory = _deps_async_session_factory()
        if factory is not None:
            async with factory() as db:
                rstate = await _rehydrate_state_from_db(db, quiz_id)
            if rstate is not None and rstate.get("final_result"):
                return rstate
    except Exception:
        logger.debug("agent.final_result_probe.db_fail", quiz_id=str(quiz_id))
    return None


def _deps_async_session_factory():
    # Indirection so tests can monkeypatch the factory in one place.
    from app.api import dependencies as _deps
    return _deps.async_session_factory


async def _start_durable_job(session_id: uuid.UUID) -> "asyncio.Task":
    """Mark the run in-flight (attempts++ on the existing row, or create it) and
    spawn the liveness heartbeat task. The row may already exist — created
    synchronously in the handler before the 202, or on a recovery re-run."""
    await _quiz_job_update(session_id, "running")
    return asyncio.create_task(_heartbeat_loop(session_id, _heartbeat_interval_s()))


async def run_agent_in_background(
    state: GraphState | AgentGraphStateModel,
    redis_client: Any,
    agent_graph: object,
) -> None:
    """Stream the agent in the background and persist the final snapshot to Redis."""
    state_dict: GraphState = state.model_dump() if hasattr(state, "model_dump") else dict(state)  # type: ignore

    session_id = state_dict.get("session_id")
    session_id_str = str(session_id)
    structlog.contextvars.bind_contextvars(trace_id=state_dict.get("trace_id"))
    cache_repo = CacheRepository(redis_client)

    logger.info(
        "Starting agent graph in background",
        quiz_id=session_id_str,
        state_keys=list(state_dict.keys()),
        ready_for_questions=bool(state_dict.get("ready_for_questions")),
    )

    final_state: GraphState = state_dict
    steps = 0
    t_start = time.perf_counter()
    job_ok = False
    job_error: str | None = None
    job_exc: BaseException | None = None  # Hitlist #8 — classify transient vs deterministic
    is_uuid = bool(session_id) and isinstance(session_id, uuid.UUID)

    # Idempotency short-circuit (audit P1): if this quiz is ALREADY finalized
    # (a recovery re-run of a quiz whose original run persisted final_result but
    # crashed before mark_succeeded), do NOT re-stream the graph — that would
    # make fresh paid decision/finalization + image calls and overwrite the
    # result the user already saw. Mark the durable job succeeded and return.
    if is_uuid:
        finished = await _load_state_with_final_result(session_id, redis_client)
        if finished is not None:
            logger.info(
                "Agent run skipped: quiz already finalized", quiz_id=session_id_str
            )
            await _quiz_job_update(session_id, "succeeded")
            structlog.contextvars.clear_contextvars()
            return

    # Durably mark this run in-flight (so a worker death leaves a recoverable
    # row) and start the liveness heartbeat (so a slow-but-alive run isn't
    # mis-claimed). Returns the heartbeat task to cancel in the finally (P1).
    heartbeat_task = await _start_durable_job(session_id) if is_uuid else None

    try:
        config = {"configurable": {"thread_id": session_id_str}}
        logger.debug("Agent background stream starting", quiz_id=session_id_str)

        async for _ in agent_graph.astream(state_dict, config=config):  # type: ignore[attr-defined]
            steps += 1

        final_state_snapshot = await agent_graph.aget_state(config)  # type: ignore[attr-defined]
        final_state = final_state_snapshot.values
        job_ok = True

        duration_ms = round((time.perf_counter() - t_start) * 1000, 1)
        logger.info(
            "Agent graph finished in background",
            quiz_id=session_id_str,
            steps=steps,
            duration_ms=duration_ms,
        )

    except Exception as e:
        job_error = str(e)
        job_exc = e
        logger.error(
            "Agent graph failed in background",
            quiz_id=session_id_str,
            error=str(e),
            **_exc_details(),
            exc_info=True,
        )
        try:
            if isinstance(final_state, dict) and "messages" in final_state:
                final_state["messages"].append(HumanMessage(content=f"Agent failed with error: {e}"))
        except Exception:
            pass
    finally:
        # Stop the liveness heartbeat BEFORE the terminal mark so it can't race
        # the succeeded/failed write or refresh a heartbeat after completion.
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except (asyncio.CancelledError, Exception):
                pass

        # Persist results
        await _save_final_state_to_cache(cache_repo, session_id_str, final_state)

        if is_uuid:
            await _persist_baseline_questions(session_id, session_id_str, final_state)
            await _persist_adaptive_and_final(session_id, session_id_str, final_state)
            # Close out the durable job, classifying a failure BEFORE the
            # terminal mark (Hitlist #8 — transient -> recoverable, deterministic
            # -> failed-fast). A process CRASH skips this finally entirely,
            # leaving 'running' for the sweeper to recover.
            await _finalize_durable_job(
                session_id, job_ok=job_ok, job_exc=job_exc, job_error=job_error
            )

        structlog.contextvars.clear_contextvars()


# ---------------------------------------------------------------------------
# Start Quiz Helpers (Extracted to fix C901)
# ---------------------------------------------------------------------------

async def _stream_characters_until_budget(
    agent_graph: object,
    config: dict,
    initial_state: GraphState,
    session_id: uuid.UUID,
    category: str,
    db_session: AsyncSession,
    stream_budget_s: float,
) -> GraphState:
    """Streams the graph until characters appear or budget runs out."""
    state = initial_state
    t_stream_start = time.perf_counter()
    steps = 0

    async for _ in agent_graph.astream(state, config=config):  # type: ignore[attr-defined]
        steps += 1
        current = await agent_graph.aget_state(config)  # type: ignore[attr-defined]
        current_values: GraphState = current.values
        have_characters = bool(current_values.get("generated_characters"))

        if have_characters:
            # Gate still closed; ensure it stays that way
            current_values["ready_for_questions"] = False
            # We don't need to save to cache here; start_quiz main flow handles final response logic,
            # but saving here ensures if we crash before return, data is safe.
            # Note: The main flow relies on returning a response payload.

            logger.info(
                "Characters generated during start",
                quiz_id=str(session_id),
                step=steps,
                character_count=len(current_values.get("generated_characters", [])),
            )

            # Persist characters that appeared during streaming
            try:
                await _persist_initial_snapshot(
                    db_session,
                    session_id=session_id,
                    category=category,
                    synopsis=state.get("synopsis"),
                    characters=current_values.get("generated_characters") or [],
                    write_session_row=False,
                    agent_plan=None,
                )
                logger.info("Characters persisted post-stream", quiz_id=str(session_id))
            except Exception:
                logger.exception("Failed to persist characters post-stream", quiz_id=str(session_id))

            state = current_values
            break

        if (time.perf_counter() - t_stream_start) >= stream_budget_s:
            logger.warning(
                "Character generation exceeded time budget; returning synopsis-only",
                quiz_id=str(session_id),
            )
            break

    return state


def _build_start_response(
    quiz_id: uuid.UUID,
    state: GraphState
) -> FrontendStartQuizResponse:
    """Constructs the final response object from the state."""
    # Synopsis
    try:
        payload_synopsis = state.get("synopsis")
        synopsis_data = _as_payload_dict(payload_synopsis, "synopsis")
        synopsis_payload = StartQuizPayload(type="synopsis", data=synopsis_data)
    except ValidationError as ve:
        logger.error("Validation error building StartQuizPayload", quiz_id=str(quiz_id), exc_info=True)
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=ve.errors()) from ve

    # Characters
    characters = state.get("generated_characters", []) or []
    characters_payload = None
    if characters:
        try:
            characters_payload = CharactersPayload(
                data=[_character_to_dict(c) for c in characters]
            )
        except ValidationError:
            # Don’t fail start; just omit characters if they’re malformed
            logger.error("Validation error building CharactersPayload", quiz_id=str(quiz_id), exc_info=True)
            characters_payload = None

    logger.info(
        "Quiz session ready for client",
        quiz_id=str(quiz_id),
        has_characters=bool(characters_payload),
        character_count=len(characters) if characters else 0,
    )

    return FrontendStartQuizResponse(
        quiz_id=quiz_id,
        initial_payload=synopsis_payload,
        characters_payload=characters_payload,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

def _build_initial_graph_state(
    quiz_id: uuid.UUID,
    trace_id: str,
    category: str,
    rejected_interpretations: list[str] | None = None,
) -> GraphState:
    """Construct the initial GraphState dict for a new quiz session.

    ``rejected_interpretations`` ("try a different interpretation" reload,
    2026-07-02): prior synopsis readings the user rejected for this same typed
    topic. The key is added ONLY when non-empty so a normal start's initial
    state is byte-for-byte unchanged; the bootstrap planner consumes it, and the
    field is mirrored on ``AgentGraphStateModel`` so the Redis round-trip
    preserves the rejection chain.
    """
    state: GraphState = {
        "session_id": quiz_id,
        "trace_id": trace_id,
        "category": category,
        "messages": [HumanMessage(content=category)],
        "error_count": 0,
        "error_message": None,
        "is_error": False,
        "synopsis": None,
        "ideal_archetypes": [],
        "generated_characters": [],
        "generated_questions": [],
        "quiz_history": [],
        "baseline_count": 0,
        "baseline_ready": False,
        "ready_for_questions": False,
        "final_result": None,
        "last_served_index": None,
    }
    if rejected_interpretations:
        state["rejected_interpretations"] = list(rejected_interpretations)
    return state


async def _persist_start_snapshot_safe(
    db_session: AsyncSession,
    *,
    quiz_id: uuid.UUID,
    category: str,
    state: GraphState,
) -> None:
    """Best-effort persistence of the initial snapshot. Logs but never raises."""
    try:
        ideal_archetypes = state.get("ideal_archetypes") or []
        plan_from_state = state.get("agent_plan") or {}
        if not plan_from_state:
            syn_dict = _serialize_synopsis(state.get("synopsis"))
            plan_from_state = {
                "title": syn_dict.get("title", ""),
                "synopsis": syn_dict.get("summary", ""),
                "ideal_archetypes": list(ideal_archetypes),
            }

        await _persist_initial_snapshot(
            db_session,
            session_id=quiz_id,
            category=category,
            synopsis=state.get("synopsis"),
            characters=state.get("generated_characters") or [],
            write_session_row=True,
            agent_plan=plan_from_state,
        )
    except Exception:
        logger.exception("Failed to persist initial session snapshot", quiz_id=str(quiz_id))


def _schedule_image_jobs_safe(
    background_tasks: BackgroundTasks,
    *,
    quiz_id: uuid.UUID,
    category: str,
    state: GraphState,
) -> None:
    """Schedule image-generation background jobs. Logs but never raises."""
    try:
        # Image pipeline keys the brand-fallback ladder off ``analysis.is_media``.
        # The bootstrap agent path writes the dict under ``topic_analysis`` and
        # leaves ``analysis`` empty; the precompute short-circuit path writes
        # it under ``analysis``. Read both so branded topics route through the
        # name+source FAL prompt regardless of which path produced the state.
        analysis_payload = (
            state.get("analysis")
            or state.get("topic_analysis")
            or {}
        )
        syn_obj = state.get("synopsis")
        chars_obj = list(state.get("generated_characters") or [])
        if syn_obj is None and not chars_obj:
            return
        # Blackbox fix #4(a) — coalesce the synopsis + character image jobs into a
        # SINGLE background task that runs them CONCURRENTLY via asyncio.gather.
        # Previously they were two sequential ``add_task`` entries with the
        # synopsis FIRST, so the (slow, single FLUX-dev) synopsis hero blocked the
        # cast fan-out from starting — the cast images often "never loaded" before
        # the user proceeded. Running them together starts the cast fan-out
        # immediately. Still fully fail-open (each job already swallows its own
        # errors; the wrapper additionally guards gather).
        background_tasks.add_task(
            _run_image_jobs_concurrently,
            quiz_id=quiz_id,
            category=category,
            analysis_payload=analysis_payload,
            synopsis=syn_obj,
            characters=chars_obj,
        )
    except Exception:
        logger.exception("Failed to schedule image jobs", quiz_id=str(quiz_id))


async def _run_image_jobs_concurrently(
    *,
    quiz_id: uuid.UUID,
    category: str,
    analysis_payload: dict,
    synopsis: Any,
    characters: list,
) -> None:
    """Run the cast + synopsis image jobs CONCURRENTLY (blackbox #4(a)). The cast
    fan-out is listed FIRST so, even if the event loop schedules tasks in order,
    the cast starts before the slower single synopsis hero. Fully fail-open."""
    coros = []
    if characters:
        coros.append(
            _image_pipeline.generate_character_images(
                session_id=quiz_id,
                characters=characters,
                category=category,
                analysis=analysis_payload,
            )
        )
    if synopsis is not None:
        coros.append(
            _image_pipeline.generate_synopsis_image(
                session_id=quiz_id,
                synopsis=synopsis,
                category=category,
                analysis=analysis_payload,
            )
        )
    if not coros:
        return
    try:
        await asyncio.gather(*coros, return_exceptions=True)
    except Exception:
        logger.exception("Image jobs gather failed", quiz_id=str(quiz_id))


async def _short_circuit_from_pack(
    db_session: AsyncSession,
    *,
    cache_repo: CacheRepository,
    redis_client: Any,
    background_tasks: BackgroundTasks,
    quiz_id: uuid.UUID,
    trace_id: str,
    category: str,
    pack_id: uuid.UUID | None,
) -> FrontendStartQuizResponse | None:
    """§21 Phase 3 — build a /quiz/start response straight from a published
    pack, without invoking the LangGraph agent.

    Returns ``None`` (caller falls through to the agent path) when:
      - ``pack_id`` is missing;
      - the hydrator returns ``None`` (legacy synopsis-only pack, or content
        is unavailable for any reason).

    On success: persists the session snapshot, primes the Redis quiz state
    (so /proceed/next behave normally), schedules background image jobs, and
    returns the assembled :class:`FrontendStartQuizResponse`.
    """
    if pack_id is None:
        return None

    from app.services.precompute import cache as _pack_cache
    from app.services.precompute.hydrator import hydrate_pack as _hydrate_pack

    # P11 (2026-07-02) — serve-path pack cache. Assembling a HydratedPack
    # costs ~5 serial DB round-trips (pack → synopsis → character_set →
    # characters → baseline questions) on EVERY /quiz/start for a popular
    # topic. Cache the fully hydrated pack in Redis (keyed by pack_id, 1h
    # TTL) so repeat hits serve from one Redis GET. Fail-open by design:
    # any cache fault degrades to the DB hydrate below.
    pack_cache_hit = False
    hydrated = await _pack_cache.get_hydrated_pack(redis_client, pack_id)
    if hydrated is not None:
        pack_cache_hit = True
    else:
        hydrated = await _hydrate_pack(db_session, pack_id=pack_id)
        if hydrated is not None:
            await _pack_cache.set_hydrated_pack(redis_client, hydrated)
    if hydrated is None:
        logger.info(
            "precompute.start.short_circuit.skip_no_content",
            quiz_id=str(quiz_id),
            pack_id=str(pack_id),
        )
        return None

    # Synthesize a GraphState dict matching what the agent would have
    # produced after _bootstrap_node + _generate_characters_node. Downstream
    # helpers (response builder, snapshot persistence, image scheduler) all
    # accept dicts via _character_to_dict / _serialize_synopsis.
    state: GraphState = _build_initial_graph_state(quiz_id, trace_id, category)
    state["synopsis"] = dict(hydrated.synopsis)
    state["generated_characters"] = [dict(c) for c in hydrated.characters]
    # Re-run the local (LLM-free) topic heuristic so the image scheduler
    # can see ``is_media`` for precomputed branded packs. Without this, the
    # short-circuit path always hit the non-branded prompt and produced
    # "someone who looks vaguely like Han Solo" instead of Han Solo.
    try:
        from app.agent.tools.intent_classification import (
            analyze_topic as _analyze_topic,
        )
        _analysis = _analyze_topic(category) or {}
    except Exception:
        _analysis = {}
    state["analysis"] = _analysis
    state["topic_analysis"] = _analysis
    state["agent_plan"] = {
        "title": hydrated.synopsis.get("title", ""),
        "synopsis": hydrated.synopsis.get("summary", ""),
        "ideal_archetypes": [c["name"] for c in hydrated.characters],
        "source": "precompute",
        "pack_id": str(pack_id),
    }

    # §21 Phase 4 — if the pack also carries pre-baked baseline questions,
    # populate them straight into state so the first /quiz/proceed call can
    # short-circuit the question-generation node entirely. Empty tuple is
    # the legacy v2 case: state stays without ``baseline_ready`` so /proceed
    # falls through to the live agent.
    if hydrated.baseline_questions:
        state["generated_questions"] = [dict(q) for q in hydrated.baseline_questions]
        state["baseline_count"] = len(hydrated.baseline_questions)
        state["baseline_ready"] = True

    # Drop any malformed character entries (defensive — pre-baked content
    # shouldn't have empty profile_text, but the CHECK constraint is still
    # there).
    _drop_invalid_characters_from_state(state, quiz_id=quiz_id)
    if not state.get("generated_characters"):
        return None

    # Persist + cache before scheduling background image jobs (the FAL tasks
    # UPDATE the same session_history row that _persist_initial_snapshot
    # creates).
    await cache_repo.save_quiz_state(state)
    await _persist_start_snapshot_safe(
        db_session, quiz_id=quiz_id, category=category, state=state
    )
    _schedule_image_jobs_safe(
        background_tasks, quiz_id=quiz_id, category=category, state=state
    )

    logger.info(
        "precompute.start.short_circuit",
        quiz_id=str(quiz_id),
        pack_id=str(pack_id),
        topic_id=str(hydrated.topic_id),
        characters=len(hydrated.characters),
        baseline_questions=len(hydrated.baseline_questions),
        pack_cache_hit=pack_cache_hit,
    )
    return _build_start_response(quiz_id, state)


# §R16 — per-IP /quiz/start throttle (AC-PROD-R16-IPLIMIT-1..3).
# Bucketed by client IP only (independent of the global per-(IP,prefix)
# bucket that gates ALL /api/quiz/* traffic together). Evaluated as a
# dependency so it runs BEFORE verify_turnstile and the LLM agent — a
# blocked attacker never round-trips to Cloudflare or our model providers.
# Fail-open on Redis errors; the global middleware bucket still applies.
async def _enforce_quiz_start_ip_rate_limit(http_request: Request) -> None:
    sec = getattr(settings, "security", None)
    cfg = getattr(sec, "quiz_start_rate_limit", None) if sec is not None else None
    if cfg is None or not getattr(cfg, "enabled", False):
        return
    try:
        from app.api.dependencies import get_redis_client as _get_redis
        # Honour FastAPI dep overrides (used heavily in unit tests).
        override = http_request.app.dependency_overrides.get(_get_redis)
        if override is not None:
            import inspect as _inspect
            res = override()
            if _inspect.isawaitable(res):
                res = await res
            redis = res
        else:
            redis = _get_redis()
        limiter = RateLimiter(
            redis=redis,
            capacity=cfg.capacity,
            refill_per_second=cfg.refill_per_second,
        )
        ip = _client_ip(http_request)
        result = await limiter.check(f"rl:quiz_start:{ip}")
    except HTTPException:
        raise
    except Exception:
        logger.warning("quiz_start.rate_limit.fail_open", exc_info=True)
        return
    if not result.allowed:
        logger.info(
            "quiz_start.rate_limited",
            # Hitlist #6 — hashed IP, never raw (privacy posture).
            client_ip_hash=_hashed_client_ip(http_request),
            retry_after=result.retry_after_s,
        )
        raise coded_http_exception(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many quiz starts from this network. Please slow down.",
            code=QF_QUIZ_START_RATE_LIMITED,
            headers={
                "Retry-After": str(max(1, result.retry_after_s)),
                "X-RateLimit-Limit": str(cfg.capacity),
                "X-RateLimit-Remaining": "0",
            },
        )


# "Try a different interpretation" (owner blackbox, 2026-07-02) — per-chain cap
# on reinterpret reloads. Every reinterpret is a FULL paid agent run admitted
# through the same Turnstile + per-IP throttle + $-ceiling gates as a normal
# /quiz/start, but a single synopsis screen could otherwise cycle interpretations
# indefinitely. Two layers, both returning a clear 429 (QF-REINTERPRET-CAP):
#   1. Deterministic: the rejected-list length may never exceed the cap (the FE
#      accumulates one entry per cycle, so length == cycle number).
#   2. Server-side: a per-(hashed-IP, topic) Redis counter bounds the chain even
#      if a client replays a short rejected list. Fail-open on Redis faults
#      (consistent with the other best-effort counters; Turnstile + per-IP
#      throttle + the $ breaker remain the front line).
def _reinterpret_cap() -> int:
    try:
        return max(1, int(getattr(getattr(settings, "quiz", None), "max_reinterprets_per_chain", 3)))
    except Exception:
        return 3


def _reinterpret_cap_429(cap: int) -> HTTPException:
    return coded_http_exception(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=(
            "You've cycled through the maximum number of interpretations for "
            "this topic. Try rewording it for a fresh angle!"
        ),
        code=QF_REINTERPRET_CAP,
        headers={
            "Retry-After": "3600",
            "X-RateLimit-Limit": str(cap),
            "X-RateLimit-Remaining": "0",
        },
    )


async def _enforce_reinterpret_chain_cap(
    redis_client: Any,
    http_request: Request,
    *,
    category: str,
    rejected_interpretations: list[str],
) -> None:
    if not rejected_interpretations:
        return
    cap = _reinterpret_cap()

    # Layer 1 — deterministic length check (cannot fail open).
    if len(rejected_interpretations) > cap:
        logger.info(
            "quiz.reinterpret.cap_exceeded",
            reason="rejected_list_length",
            count=len(rejected_interpretations),
            cap=cap,
            client_ip_hash=_hashed_client_ip(http_request),
        )
        raise _reinterpret_cap_429(cap)

    # Layer 2 — per-(hashed IP, topic) chain counter (best-effort, fail-open).
    topic_hash = hashlib.sha256(
        " ".join(category.split()).casefold().encode("utf-8")
    ).hexdigest()[:16]
    key = f"reinterpret:{_hashed_client_ip(http_request)}:{topic_hash}"
    try:
        n = int(await redis_client.incr(key))
        if n == 1:
            await redis_client.expire(key, 3600)
    except Exception:
        logger.debug("quiz.reinterpret.chain_counter.fail_open")
        return
    if n > cap:
        logger.info(
            "quiz.reinterpret.cap_exceeded",
            reason="chain_counter",
            count=n,
            cap=cap,
            client_ip_hash=_hashed_client_ip(http_request),
        )
        raise _reinterpret_cap_429(cap)


def _live_cost_503() -> HTTPException:
    return coded_http_exception(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Quiz creation is temporarily at capacity. Please try again later.",
        code=QF_COST_CEILING,
        headers={"Retry-After": "3600"},
    )


async def _enforce_global_daily_cost_ceiling(  # noqa: C901 — linear breaker: read-check + reserve/re-check + local-fallback + count-backstop
    redis_client: Any, *, is_start: bool = True
) -> int:
    """Cluster-wide hard daily ceiling on the LIVE paid pipeline — a runaway-cost
    circuit breaker that bounds AGGREGATE LLM+FAL spend even when a distributed/
    botnet attack spreads across many real IPs (the per-IP and per-session caps
    only bound a single source).

    Hitlist #2 (2026-06-30): this is a DOLLAR breaker. Every LLM call records its
    real ``litellm.completion_cost`` and every FAL image records
    ``fal_image_cost_usd`` into a Redis daily CENTS counter (see
    :mod:`app.services.cost_meter`). When that aggregate exceeds
    ``daily_budget_usd`` the breaker trips. Unlike the old start-only count, this
    gates /quiz/start AND the paid follow-ups /quiz/proceed + /quiz/next
    (``is_start=False``) so adaptive questions + finalization draw down the same
    budget.

    Hitlist #1 (2026-06-30): RESERVE an estimated per-quiz cost via an atomic
    INCRBY at admission (``/start`` only), then re-check the ceiling against the
    reserved total. Previously the breaker only READ the counter and spend was
    recorded AFTER the agent ran, so a concurrent burst — all reading the same
    pre-burst total — was admitted en masse and overshot the daily ceiling. The
    reservation makes concurrent admissions see each other (soft -> near-hard).
    The returned reserved-cents value is RECONCILED (released) by the
    ``/quiz/start`` handler itself: that endpoint runs the paid agent INLINE
    (within the request, returning 201) — it does NOT use
    ``run_agent_in_background`` — so by the time it returns the per-call meter has
    already accrued the real spend, and the handler's ``finally`` releases the
    reservation (``reconcile_reservation(actual=0)``), leaving only the real
    metered spend. Returns the reserved cents (0 when nothing was reserved).

    Hitlist #3 (2026-06-30): when the counter read returns ``None`` (Redis
    unreachable) AND a budget is configured, consult a PROCESS-LOCAL fallback
    start cap (``/start`` only) so a sustained Redis outage cannot remove every $
    ceiling. This DEGRADES (a coarse per-replica cap), it does not fail closed —
    a brief blip still admits real users.

    A coarse start-count (``max_quiz_starts_per_day``) is retained as a SECONDARY
    backstop, evaluated only on /start.

    FAIL OPEN (hard contract): a counter/Redis fault must NEVER break legitimate
    traffic — the per-IP + per-session caps remain the front line; this is
    defense-in-depth. Any read/incr error is swallowed and the request proceeds
    (the local fallback is the only added coarse cap during a full outage).
    """
    cfg = getattr(getattr(settings, "security", None), "live_cost_guard", None)
    if cfg is None or not getattr(cfg, "enabled", False):
        return 0

    from datetime import datetime, timezone

    day = datetime.now(timezone.utc).strftime("%Y%m%d")

    # PRIMARY — dollar breaker (gates start, proceed and next). Read the already-
    # accrued spend; the meter increments per LLM/FAL call. Fail-open: a None
    # read (Redis down / missing) never blocks, but a configured budget routes a
    # None read on /start to the process-local fallback cap (Hitlist #3).
    reserved_cents = 0
    try:
        budget_usd = float(getattr(cfg, "daily_budget_usd", 0.0) or 0.0)
    except Exception:
        budget_usd = 0.0
    if budget_usd > 0:
        from app.services import cost_meter
        budget_cents = int(round(budget_usd * 100.0))
        spent_cents = await cost_meter.read_daily_cents(redis_client)
        if spent_cents is None:
            # Redis unreachable for the $ counter — the cluster-wide breaker is
            # blind. On /start, fall back to the coarse per-replica in-memory cap
            # (degrade, not fail-closed) so a sustained outage still bounds spend.
            # We do NOT short-circuit here: the SECONDARY count guard below uses a
            # separate Redis counter that may still be reachable, so we fall
            # through to it after the local check (which itself fails open).
            if is_start:
                from app.services import local_fallback_limiter
                if not local_fallback_limiter.allow_start():
                    logger.warning(
                        "quiz.live_cost_ceiling.local_fallback_tripped",
                        reason="redis_outage_local_cap",
                        day=day,
                    )
                    raise _live_cost_503()
            # No readable counter to reserve against while Redis is down; skip the
            # reservation (and its post-reservation re-check) and fall through.
        else:
            if spent_cents >= budget_cents:
                logger.warning(
                    "quiz.live_cost_ceiling.exceeded",
                    reason="daily_budget_usd",
                    spent_cents=spent_cents,
                    budget_usd=budget_usd,
                    is_start=is_start,
                    day=day,
                )
                raise _live_cost_503()

            # Reservation (Hitlist #1) — only on /start (paid follow-ups don't
            # reserve), and only when an estimate is configured AND the counter
            # was readable. Reserve, then re-check against the post-reservation
            # total so a burst that collectively crosses the ceiling is caught:
            # the request whose reservation pushes the counter over the line is
            # rejected and its reservation released. Fail-open: a reservation
            # fault returns None and we proceed without a reservation (nothing to
            # reconcile).
            if is_start:
                try:
                    est_usd = float(
                        getattr(cfg, "reservation_estimate_usd", 0.0) or 0.0
                    )
                except Exception:
                    est_usd = 0.0
                est_cents = cost_meter._usd_to_cents(est_usd) if est_usd > 0 else 0
                if est_cents > 0:
                    new_total = await cost_meter.reserve_estimated_cents(
                        redis_client, est_cents
                    )
                    if new_total is not None:
                        reserved_cents = est_cents
                        if new_total >= budget_cents:
                            # This reservation tipped us over — reject and release
                            # it so a rejected request never permanently consumes
                            # budget.
                            logger.warning(
                                "quiz.live_cost_ceiling.exceeded",
                                reason="daily_budget_usd_reserved",
                                reserved_total_cents=new_total,
                                budget_usd=budget_usd,
                                is_start=is_start,
                                day=day,
                            )
                            await cost_meter.reconcile_reservation(
                                redis_client,
                                estimated_cents=reserved_cents,
                                actual_cents=0,
                            )
                            raise _live_cost_503()

    # SECONDARY — coarse start-count backstop (only meaningful on /start).
    if not is_start:
        return reserved_cents
    cap = int(getattr(cfg, "max_quiz_starts_per_day", 0) or 0)
    if cap <= 0:
        return reserved_cents
    key = f"live_spend:quiz_starts:{day}"
    try:
        n = int(await redis_client.incr(key))
        if n == 1:
            await redis_client.expire(key, 90_000)  # ~25h, spans the UTC day
    except Exception:
        return reserved_cents
    if n > cap:
        logger.warning(
            "quiz.live_cost_ceiling.exceeded",
            reason="max_quiz_starts_per_day",
            count=n,
            cap=cap,
            day=day,
        )
        # Release the admission reservation — this request is rejected and must
        # not permanently consume budget.
        if reserved_cents > 0:
            from app.services import cost_meter
            await cost_meter.reconcile_reservation(
                redis_client, estimated_cents=reserved_cents, actual_cents=0
            )
        raise _live_cost_503()
    return reserved_cents


@router.post(
    "/quiz/start",
    response_model=FrontendStartQuizResponse,
    summary="Start a new quiz session",
    status_code=status.HTTP_201_CREATED,
)
async def start_quiz(  # noqa: C901 — orchestrator: budget/lookup/cache/agent branches are inherent
    request: StartQuizRequest,
    http_request: Request,
    background_tasks: BackgroundTasks,
    agent_graph: Annotated[object, Depends(get_agent_graph)],
    redis_client: Annotated[Any, Depends(get_redis_client)],
    db_session: Annotated[AsyncSession, Depends(get_db_session)],
    _ip_throttle: Annotated[None, Depends(_enforce_quiz_start_ip_rate_limit)],
    turnstile_verified: Annotated[bool, Depends(verify_turnstile)],
    precompute_lookup: Annotated[
        Any, Depends(get_precompute_lookup)
    ],
):
    """
    Starts a quiz session and (within a strict time budget) waits for:
      1) Generated synopsis
      2) Attempts to stream initial character set within a separate budget
    """
    quiz_id = uuid.uuid4()
    trace_id = str(uuid.uuid4())
    cache_repo = CacheRepository(redis_client)

    # "Try a different interpretation" reload: non-empty when the user rejected
    # prior readings of this same typed topic (validated/sanitized by the model).
    rejected_interpretations = list(request.rejected_interpretations or [])
    is_reinterpret = bool(rejected_interpretations)

    structlog.contextvars.bind_contextvars(trace_id=trace_id)
    logger.info(
        "Starting new quiz session",
        quiz_id=str(quiz_id),
        category=request.category,
        is_reinterpret=is_reinterpret,
        rejected_interpretations_count=len(rejected_interpretations),
        env=settings.app.environment,
    )

    # Reinterpret chain cap — runs after Turnstile + per-IP throttle (deps) and
    # BEFORE the $-ceiling reservation, so a capped chain never consumes budget.
    await _enforce_reinterpret_chain_cap(
        redis_client,
        http_request,
        category=request.category,
        rejected_interpretations=rejected_interpretations,
    )

    # Global daily cost circuit-breaker (defense-in-depth vs distributed abuse).
    # Runs after Turnstile + per-IP throttle (deps above) so only real attempts
    # consume the budget. Hitlist #1 — returns the cents RESERVED at admission.
    # /quiz/start runs the paid agent INLINE (not in run_agent_in_background), so
    # by the time this handler returns the per-call meter has accrued the real
    # spend; we RELEASE the reservation in this handler (release_started flag +
    # finally) so the daily counter converges to true metered spend. A
    # short-circuit from a precompute pack (no paid agent run) releases it too.
    reserved_cents = await _enforce_global_daily_cost_ceiling(redis_client)
    _reservation_released = False

    async def _release_reservation() -> None:
        nonlocal _reservation_released
        if _reservation_released or reserved_cents <= 0:
            return
        _reservation_released = True
        from app.services import cost_meter
        await cost_meter.reconcile_reservation(
            redis_client, estimated_cents=reserved_cents, actual_cents=0
        )

    # §21 Phase 2 — Read-path lookup shim. When `precompute.enabled=False`
    # (the default through Phase 5 per Universal-G5) this is a no-op and the
    # response below is byte-for-byte identical to the pre-§21 behaviour.
    # Phase 3 (added) — On a HIT with a fully-baked pack we skip the agent
    # entirely (see `_short_circuit_from_pack` further down). Image jobs are
    # still scheduled in the background via the FAL pipeline.
    # Reinterpret reload: the precompute short-circuit is BYPASSED — serving the
    # pre-baked pack for this topic would return the exact interpretation the
    # user just rejected. Only reinterprets skip it; a normal start is unchanged.
    resolution = None
    if is_reinterpret:
        logger.info(
            "quiz.reinterpret.precompute_bypassed",
            quiz_id=str(quiz_id),
            rejected_interpretations_count=len(rejected_interpretations),
        )
    elif getattr(getattr(settings, "precompute", None), "enabled", False):
        try:
            resolution = await precompute_lookup.resolve_topic(request.category)
            logger.info(
                "precompute.start.lookup",
                quiz_id=str(quiz_id),
                category=request.category,
                hit=resolution is not None,
                via=getattr(resolution, "via", None),
                topic_id=str(getattr(resolution, "topic_id", "") or "") or None,
                pack_id=str(getattr(resolution, "pack_id", "") or "") or None,
            )
            # P11 (2026-07-02) — the former Phase-4 `get_or_fill` +
            # `Link: rel=preload` block was deleted here: `_hydrate_resolved_pack`
            # always returned an empty ``storage_uris`` tuple (pack→media linkage
            # never landed), so the extra Redis round-trip + DB query could never
            # emit a header. The serve-path cache now lives inside
            # ``_short_circuit_from_pack`` (full HydratedPack, keyed by pack_id).
        except Exception:
            # Lookup is advisory — never break /quiz/start because the shim
            # tripped over a transient DB / Redis fault.
            logger.exception("precompute.start.lookup_error", quiz_id=str(quiz_id))

    # §21 Phase 3 — short-circuit. When the resolver hit a pack that carries
    # both synopsis text and at least one Character row, we can build the
    # /quiz/start response without invoking the LangGraph agent at all. This
    # is the dominant path in production for popular categories, and it
    # eliminates the ~30s LLM round-trip while leaving image generation on
    # the existing FAL background pipeline.
    if resolution is not None:
        try:
            short = await _short_circuit_from_pack(
                db_session,
                cache_repo=cache_repo,
                redis_client=redis_client,
                background_tasks=background_tasks,
                quiz_id=quiz_id,
                trace_id=trace_id,
                category=request.category,
                pack_id=getattr(resolution, "pack_id", None),
            )
            if short is not None:
                # Non-paid short-circuit: release the admission reservation.
                await _release_reservation()
                return short
        except Exception:
            # Fall back to the live agent path on any unexpected error so
            # users still get an experience even if the precompute layer is
            # misbehaving.
            logger.exception(
                "precompute.start.short_circuit_error", quiz_id=str(quiz_id)
            )

    # Initial graph state (carries the rejected readings into the bootstrap
    # planner on a reinterpret; byte-for-byte unchanged for a normal start).
    initial_state: GraphState = _build_initial_graph_state(
        quiz_id,
        trace_id,
        request.category,
        rejected_interpretations=rejected_interpretations or None,
    )

    # Time budgets
    try:
        FIRST_STEP_TIMEOUT_S = float(getattr(getattr(settings, "quiz", None), "first_step_timeout_s", 30.0))
        STREAM_BUDGET_S = float(getattr(getattr(settings, "quiz", None), "stream_budget_s", 30.0))
    except Exception:
        FIRST_STEP_TIMEOUT_S = 30.0
        STREAM_BUDGET_S = 30.0

    try:
        # --- Step 1: get synopsis quickly ---
        config = {"configurable": {"thread_id": str(quiz_id)}}
        t0 = time.perf_counter()

        await asyncio.wait_for(  # type: ignore[attr-defined]
            agent_graph.ainvoke(initial_state, config),
            timeout=FIRST_STEP_TIMEOUT_S,
        )
        state_snapshot = await agent_graph.aget_state(config)  # type: ignore[attr-defined]
        state_after_first: GraphState = state_snapshot.values

        logger.info(
            "Agent initial step completed",
            quiz_id=str(quiz_id),
            duration_ms=round((time.perf_counter() - t0) * 1000, 1),
        )

        # Check Synopsis
        if not state_after_first.get("synopsis"):
            raise coded_http_exception(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="The AI agent failed to generate a quiz synopsis. Please try a different category.",
                code=QF_AGENT_NO_SYNOPSIS,
            )

        # AC-START-11 — drop characters whose ``profile_text`` is empty before
        # any persistence, response building, or background image scheduling.
        # This prevents one failed-LLM character from violating the
        # ``characters_profile_text_check`` CHECK constraint and rolling back
        # the entire ``session_history`` insert (which would silently break
        # ``/quiz/{id}/media`` for the rest of the session).
        _drop_invalid_characters_from_state(state_after_first, quiz_id=quiz_id)

        # Save & Persist Synopsis
        await cache_repo.save_quiz_state(state_after_first)

        await _persist_start_snapshot_safe(
            db_session,
            quiz_id=quiz_id,
            category=request.category,
            state=state_after_first,
        )

        # --- Step 2: Stream characters if needed ---
        if not state_after_first.get("generated_characters"):
            state_after_first = await _stream_characters_until_budget(
                agent_graph, config, state_after_first, quiz_id, request.category,
                db_session, STREAM_BUDGET_S
            )
            # Re-apply the empty-profile filter — characters streamed during
            # Step 2 are subject to the same CHECK-constraint risk.
            _drop_invalid_characters_from_state(state_after_first, quiz_id=quiz_id)

        # --- Step 3: Schedule background image generation (non-blocking) ---
        _schedule_image_jobs_safe(
            background_tasks,
            quiz_id=quiz_id,
            category=request.category,
            state=state_after_first,
        )

        # --- Step 4: Build Response ---
        return _build_start_response(quiz_id, state_after_first)

    except asyncio.TimeoutError as e:
        logger.warning("Quiz start process timed out", quiz_id=str(quiz_id))
        raise coded_http_exception(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Our crystal ball is a bit cloudy and we couldn't conjure up your quiz in time. Please try another category!",
            code=QF_AGENT_TIMEOUT,
        ) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to start quiz session", quiz_id=str(quiz_id), error=str(e), exc_info=True)
        # Hitlist #4 — surface a PRECISE code for transient LLM/provider failures
        # (rate-limit / timeout / oversized / provider-down) instead of collapsing
        # every error to QF-UNKNOWN. A non-transient/unclassifiable error keeps the
        # existing catch-all 503/QF-UNKNOWN (no behaviour change there).
        qf = _qf_code_for_transient(e)
        if qf is not None:
            spec = get_spec(qf)
            raise coded_http_exception(
                status_code=spec.http_status,
                detail="An unexpected error occurred while starting the quiz. Our wizards have been notified.",
                code=qf,
            ) from e
        raise coded_http_exception(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="An unexpected error occurred while starting the quiz. Our wizards have been notified.",
            code=QF_UNKNOWN,
        ) from e
    finally:
        # Hitlist #1 — release the admission reservation once the inline paid run
        # is done (success OR failure); the per-call meter has already accrued the
        # real spend, so releasing avoids double-counting. Fail-open.
        await _release_reservation()
        structlog.contextvars.clear_contextvars()


# §R16+ (P0-1) — per-session hard cap on cost-bearing agent actions.
# A single Turnstile-solved /quiz/start otherwise lets a bot drive unbounded
# paid LangGraph runs via repeated /quiz/next on one quiz_id — the session
# lock only serializes concurrent calls; it does not bound the total. Cap the
# combined /proceed + /next actions per session to the quiz's own question
# budget plus slack. Best-effort: a counter fault must never break a real quiz
# (the per-IP limiter and the graph-level max_total_questions still apply).
async def _enforce_session_action_cap(redis_client: Any, quiz_id_str: str) -> None:
    try:
        cap = int(getattr(getattr(settings, "quiz", None), "max_total_questions", 24)) + 10
    except Exception:
        cap = 34
    key = f"quiz_actions:{quiz_id_str}"
    try:
        n = int(await redis_client.incr(key))
        if n == 1:
            await redis_client.expire(key, 3600)
    except Exception:
        return
    if n > cap:
        logger.info(
            "quiz.session_action_cap.exceeded", quiz_id=quiz_id_str, count=n, cap=cap
        )
        raise coded_http_exception(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="This quiz has reached its limit. Please start a new quiz.",
            code=QF_SESSION_ACTION_CAP,
            headers={"Retry-After": "60"},
        )


# P9 (2026-07-02) — Redis-miss fallback for the mid-quiz endpoints.
# /quiz/status already rebuilds live state from the durable Postgres snapshots
# when the Redis key has expired / been evicted (see _rehydrate_state_from_db).
# /quiz/proceed and /quiz/next previously 404'd on the same miss — a TERMINAL
# error in the FE — even though everything needed to continue is in Postgres.
async def _load_state_with_db_fallback(
    cache_repo: CacheRepository,
    db_session: AsyncSession,
    quiz_id: uuid.UUID,
    *,
    endpoint: str,
) -> dict[str, Any] | None:
    """Return the live quiz state as a plain dict, rehydrating from Postgres
    (and re-priming Redis) on a cache miss.

    Returns ``None`` only when neither Redis nor the DB knows the quiz — the
    caller raises the 404 in that case.
    """
    current_state = await cache_repo.get_quiz_state(quiz_id)
    if current_state:
        return _to_state_dict(current_state)

    rehydrated = await _rehydrate_state_from_db(db_session, quiz_id)
    if rehydrated is None:
        return None
    # Re-prime Redis so subsequent polls hit cache again and so /quiz/next's
    # atomic WATCH-based update finds the key. Best-effort: a failed re-prime
    # must not fail the request (the atomic update then surfaces a retryable
    # 409 instead).
    try:
        await cache_repo.save_quiz_state(rehydrated)
    except Exception:
        logger.debug(
            "quiz.rehydrate.reprime_failed", quiz_id=str(quiz_id), endpoint=endpoint
        )
    logger.info(
        "quiz.rehydrated_from_db", quiz_id=str(quiz_id), endpoint=endpoint
    )
    return dict(rehydrated)


@router.post(
    "/quiz/proceed",
    response_model=ProcessingResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Proceed from synopsis/characters to question generation",
)
async def proceed_quiz(
    request: ProceedRequest,
    background_tasks: BackgroundTasks,
    agent_graph: Annotated[object, Depends(get_agent_graph)],
    redis_client: Annotated[Any, Depends(get_redis_client)],
    db_session: Annotated[AsyncSession, Depends(get_db_session)],
):
    """
    Advance the quiz without submitting an answer.
    This sets ready_for_questions=True and runs the agent to generate baseline questions.
    """
    cache_repo = CacheRepository(redis_client)
    quiz_id_str = str(request.quiz_id)

    # §16.3 — single-flight session lock for /proceed (AC-LOCK-PROCEED-1..3).
    # Prevents two concurrent /proceed (or one /proceed racing one /next) from
    # both flipping ``ready_for_questions`` and double-firing the agent.
    from app.security import session_lock as _sl
    _lock_token = await _sl.acquire(
        redis_client, quiz_id_str, ttl_s=int(getattr(settings.security, "session_lock_ttl_s", 10))
    )
    if _lock_token is None:
        logger.info("Concurrent /quiz/proceed rejected by session lock", quiz_id=quiz_id_str)
        raise SessionBusyError("Another request is currently being processed for this session.")

    try:
        # P9 — fall back to the durable Postgres snapshot on a Redis miss
        # (TTL expiry / eviction) instead of dead-ending the quiz with a 404.
        current_state_dict = await _load_state_with_db_fallback(
            cache_repo, db_session, request.quiz_id, endpoint="proceed"
        )
        if not current_state_dict:
            logger.warning("Quiz session not found on proceed", quiz_id=quiz_id_str)
            raise NotFoundError("Quiz session not found.")

        # P0-1 — bound total paid agent actions for this session.
        await _enforce_session_action_cap(redis_client, quiz_id_str)
        # Hitlist #2 — the dollar breaker also gates this paid follow-up (the
        # agent runs another LLM loop here). Fail-open on any metering fault.
        await _enforce_global_daily_cost_ceiling(redis_client, is_start=False)

        # Flip the questions gate and persist snapshot BEFORE scheduling background work
        current_state_dict["ready_for_questions"] = True
        await cache_repo.save_quiz_state(current_state_dict)

        structlog.contextvars.bind_contextvars(trace_id=current_state_dict.get("trace_id"))
        logger.info("Proceeding quiz; gate opened for questions", quiz_id=quiz_id_str)

        # §21 Phase 4 — short-circuit. /quiz/start may have populated state
        # with pre-baked baseline questions (``baseline_ready=True`` +
        # ``generated_questions`` filled, ``agent_plan.source='precompute'``).
        # In that case the agent has nothing to add: persist the baseline
        # blob to Postgres and return without invoking the LangGraph at all.
        # Failures fall through to the agent path so the user always gets
        # questions even if the precompute layer misbehaves.
        plan = current_state_dict.get("agent_plan") or {}
        plan_source = plan.get("source") if isinstance(plan, dict) else None
        already_ready = bool(current_state_dict.get("baseline_ready"))
        baseline_qs = list(current_state_dict.get("generated_questions") or [])
        if plan_source == "precompute" and already_ready and baseline_qs:
            try:
                background_tasks.add_task(
                    _persist_baseline_questions,
                    request.quiz_id,
                    quiz_id_str,
                    current_state_dict,
                )
                logger.info(
                    "precompute.proceed.short_circuit",
                    quiz_id=quiz_id_str,
                    pack_id=str(plan.get("pack_id")) if isinstance(plan, dict) else None,
                    baseline_count=len(baseline_qs),
                )
                structlog.contextvars.clear_contextvars()
                return ProcessingResponse(status="processing", quiz_id=request.quiz_id)
            except Exception:
                # Fall through to live agent path on any failure scheduling
                # the persistence task.
                logger.exception(
                    "precompute.proceed.short_circuit_error", quiz_id=quiz_id_str
                )

        # Durably mark the job running BEFORE scheduling + returning 202 so a
        # crash in the schedule→first-write window still leaves a recoverable row
        # (audit P1). The bg task's own mark_running bumps attempts on this row.
        await _ensure_job_row_before_schedule(db_session, request.quiz_id)

        # Schedule the agent to continue (no answer appended)
        background_tasks.add_task(run_agent_in_background, current_state_dict, redis_client, agent_graph)
        logger.info("Background task scheduled for proceed", quiz_id=quiz_id_str)

        structlog.contextvars.clear_contextvars()
        return ProcessingResponse(status="processing", quiz_id=request.quiz_id)
    finally:
        await _sl.release(redis_client, quiz_id_str, _lock_token)


# ---------------------------------------------------------------------------
# Next Question Helpers (Extracted to fix C901)
# ---------------------------------------------------------------------------

def _resolve_submitted_answer_text(
    state_dict: dict, q_index: int, request: NextQuestionRequest
) -> str | None:
    """Resolve what answer text this request WOULD record for ``q_index``,
    without mutating anything — mirrors `_validate_and_record_answer`'s
    resolution (including the shuffled-index de-map). Returns None when the
    payload can't be resolved against the stored question (missing question,
    out-of-range option, empty free text) so callers can fail open.

    Used by the duplicate-conflict check (deep-review #4): a re-submitted
    answer for an already-recorded question is only an idempotent retry when
    it resolves to the SAME text that was recorded.
    """
    try:
        server_qs = state_dict.get("generated_questions") or []
        if not (0 <= q_index < len(server_qs)):
            return None
        q = server_qs[q_index]
        if hasattr(q, "model_dump"):
            qd = q.model_dump()
        elif isinstance(q, dict):
            qd = dict(q)
        else:
            qd = QuizQuestion.model_validate(q).model_dump()
        opts = qd.get("options", []) or []
        if request.option_index is not None:
            if not (0 <= request.option_index < len(opts)):
                return None
            q_text = qd.get("question_text", "") or qd.get("text", "")
            order = _display_option_order(q_index + 1, q_text, len(opts))
            opt = opts[order[request.option_index]]
            return str((opt.get("text") if isinstance(opt, dict) else opt) or "")
        ans = (request.answer or "").strip()
        return ans[:280] if ans else None
    except Exception:
        return None


def _validate_and_record_answer(state_dict: dict, request: NextQuestionRequest) -> tuple[list[dict], list[Any]]:
    """Validates the user's answer index and updates history."""
    history = list(state_dict.get("quiz_history") or [])
    expected_index = len(history)
    q_index = request.question_index

    if q_index < expected_index:
        # Deep-review #4 (BE half, 2026-07-02): a duplicate is only safe to
        # swallow as an idempotent retry (202) when it resolves to the SAME
        # answer that was recorded. A duplicate carrying a DIFFERENT option is
        # a client desync (e.g. an answer was silently dropped and the FE's
        # local count shifted) — silently 202-ing it used to eat the user's
        # real answer. Surface 409 with the expected index so the FE can
        # resync (it now submits the served ordinal, PR #69). Unresolvable
        # payloads (garbage option_index etc.) keep the historical fail-open
        # 202 rather than growing a new 4xx surface.
        prior = history[q_index] if 0 <= q_index < len(history) else None
        would_be = _resolve_submitted_answer_text(state_dict, q_index, request)
        if (
            prior is not None
            and would_be is not None
            and would_be != str(prior.get("answer_text") or "")
        ):
            raise coded_http_exception(
                status_code=409,
                detail=(
                    f"A different answer was already recorded for question "
                    f"{q_index + 1}; the next expected question_index is "
                    f"{expected_index}."
                ),
                code=QF_QUIZ_STALE_ANSWER,
            )
        # Idempotent duplicate — handled by caller (202 processing).
        raise ValueError("DUPLICATE")

    if q_index > expected_index:
        raise coded_http_exception(
            status_code=409,
            detail="Stale or out-of-order answer.",
            code=QF_QUIZ_STALE_ANSWER,
        )

    server_qs = state_dict.get("generated_questions") or []
    if q_index < 0 or q_index >= len(server_qs):
        raise coded_http_exception(
            status_code=400,
            detail="question_index out of range.",
            code=QF_QUIZ_BAD_ANSWER,
        )

    q = server_qs[q_index]
    # Normalize question
    if hasattr(q, "model_dump"):
        qd = q.model_dump()
    elif isinstance(q, dict):
        qd = dict(q)
    else:
        qd = QuizQuestion.model_validate(q).model_dump()

    option_index = request.option_index
    ans_text = (request.answer or "").strip()
    opts = qd.get("options", []) or []
    # Canonical (original-order) index actually recorded — see de-map below. For
    # free-text / no-option paths it stays None.
    recorded_option_index: int | None = None

    if option_index is not None:
        if option_index < 0 or option_index >= len(opts):
            raise coded_http_exception(
                status_code=400,
                detail="option_index out of range.",
                code=QF_QUIZ_BAD_ANSWER,
            )
        # The client's `option_index` is a position in the SHUFFLED order it was
        # served (see `_format_next_question`). De-map it back to the original
        # stored option through the identical permutation before recording —
        # otherwise the recorded answer is a different option than the user
        # picked (~75% wrong with 4 options). Seed inputs mirror the serve path
        # exactly: question_number = q_index + 1, and the same question_text.
        q_text_for_seed = qd.get("question_text", "") or qd.get("text", "")
        order = _display_option_order(q_index + 1, q_text_for_seed, len(opts))
        recorded_option_index = order[option_index]
        # Server-controlled option text only — never the raw client string.
        ans_text = str(opts[recorded_option_index].get("text") or "")
    elif opts:
        # P1 — prompt-injection guard. The question offers options, so a
        # free-text-only answer is not a legitimate client action (the UI always
        # sends option_index). Accepting it would interpolate arbitrary user
        # text verbatim into the next-question and final-profile LLM prompts
        # (output steering / system-prompt exfiltration + token burn). Require
        # selecting one of the provided options.
        raise coded_http_exception(
            status_code=400,
            detail="Please select one of the provided options.",
            code=QF_QUIZ_BAD_ANSWER,
        )
    else:
        # No options (a genuinely free-text question, if ever introduced): keep
        # the text but cap it well below the 2048 transport limit so it cannot
        # be used to balloon token usage.
        ans_text = ans_text[:280]

    new_history = [*history, {
        "question_index": q_index,
        "question_text": qd.get("question_text", ""),
        "answer_text": ans_text,
        # Canonical (original stored order) index, consistent with answer_text —
        # NOT the shuffled display index the client sent.
        "option_index": recorded_option_index,
    }]

    new_messages = list(state_dict.get("messages") or [])
    new_messages.append(HumanMessage(content=f"Answer to Q{q_index+1}: {ans_text}"))

    return new_history, new_messages


@router.post(
    "/quiz/next",
    response_model=ProcessingResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit an answer and get next question",
)
async def next_question(
    request: NextQuestionRequest,
    background_tasks: BackgroundTasks,
    agent_graph: Annotated[object, Depends(get_agent_graph)],
    redis_client: Annotated[Any, Depends(get_redis_client)],
    db_session: Annotated[AsyncSession, Depends(get_db_session)],
):
    """Append the user's answer to the conversation and continue the agent in the background."""
    cache_repo = CacheRepository(redis_client)
    quiz_id_str = str(request.quiz_id)

    logger.info("Submitting answer for session", quiz_id=quiz_id_str)

    # §15.4 — single-flight session lock (AC-LOCK-1..5).
    from app.security import session_lock as _sl
    _lock_token = await _sl.acquire(
        redis_client, quiz_id_str, ttl_s=int(getattr(settings.security, "session_lock_ttl_s", 10))
    )
    if _lock_token is None:
        logger.info("Concurrent /quiz/next rejected by session lock", quiz_id=quiz_id_str)
        raise SessionBusyError("Another request is currently being processed for this session.")

    try:
        # P9 — fall back to the durable Postgres snapshot on a Redis miss
        # (TTL expiry / eviction) instead of dead-ending the quiz with a 404.
        state_dict = await _load_state_with_db_fallback(
            cache_repo, db_session, request.quiz_id, endpoint="next"
        )
        if not state_dict:
            raise NotFoundError("Quiz session not found.")

        # P0-1 — bound total paid agent actions for this session.
        await _enforce_session_action_cap(redis_client, quiz_id_str)
        # Hitlist #2 — the dollar breaker also gates this paid follow-up (the
        # agent may run another LLM loop / finalization here). Fail-open.
        await _enforce_global_daily_cost_ceiling(redis_client, is_start=False)

        structlog.contextvars.bind_contextvars(trace_id=state_dict.get("trace_id"))

        # Validate and update state
        try:
            new_history, new_messages = _validate_and_record_answer(state_dict, request)
        except ValueError as e:
            if str(e) == "DUPLICATE":
                logger.info("Duplicate answer received", quiz_id=quiz_id_str)
                structlog.contextvars.clear_contextvars()
                return ProcessingResponse(status="processing", quiz_id=request.quiz_id)
            raise coded_http_exception(
                status_code=400,
                detail="Invalid answer state.",
                code=QF_QUIZ_BAD_ANSWER,
            ) from e
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Failed to record answer", quiz_id=quiz_id_str, error=str(e), exc_info=True)
            raise coded_http_exception(
                status_code=400,
                detail="Invalid answer payload.",
                code=QF_QUIZ_BAD_ANSWER,
            ) from e

        # Persist atomic update
        updated_state = await cache_repo.update_quiz_state_atomically(request.quiz_id, {
            "quiz_history": new_history,
            "messages": new_messages,
            "ready_for_questions": True,
        })
        if updated_state is None:
            raise coded_http_exception(
                status_code=409,
                detail="Please retry answer submission.",
                code=QF_SESSION_BUSY,
            )

        updated_state_dict = _to_state_dict(updated_state)

        # Snapshot history to DB (best-effort)
        try:
            sess_repo = SessionRepository(db_session)
            await sess_repo.update_qa_history(
                session_id=request.quiz_id,
                qa_history=jsonable_encoder(updated_state_dict.get("quiz_history") or []),
            )
            await db_session.commit()
        except Exception:
            logger.debug("Non-fatal: failed to snapshot qa_history", quiz_id=quiz_id_str)

        # Trigger Background Agent if needed
        new_answered = len(updated_state_dict.get("quiz_history") or [])
        baseline_count = int(updated_state_dict.get("baseline_count") or 0)
        if new_answered >= baseline_count:
            # Durably mark the job running BEFORE scheduling + returning 202 so a
            # crash in the schedule→first-write window still leaves a recoverable
            # row (audit P1). Same held session as the qa_history snapshot above.
            await _ensure_job_row_before_schedule(db_session, request.quiz_id)
            background_tasks.add_task(run_agent_in_background, updated_state_dict, redis_client, agent_graph)
            logger.info("Background task scheduled", quiz_id=quiz_id_str)

        structlog.contextvars.clear_contextvars()
        return ProcessingResponse(status="processing", quiz_id=request.quiz_id)
    finally:
        await _sl.release(redis_client, quiz_id_str, _lock_token)


# ---------------------------------------------------------------------------
# Quiz Status Helpers (Extracted to fix C901)
# ---------------------------------------------------------------------------

def _display_option_order(question_number: int | None, question_text: str, n: int) -> list[int]:
    """Deterministic answer-option display permutation — the SINGLE source of
    truth shared by the serve path (`_format_next_question`) and the record path
    (`_validate_and_record_answer`).

    Returns a list ``order`` of length ``n`` where ``order[display_pos]`` is the
    index of the option in the ORIGINAL (stored) option list that is shown at
    ``display_pos``. Concretely, serving reorders options as
    ``[opts[order[p]] for p in range(n)]`` and recording maps the client's
    displayed ``option_index`` back to the original option via
    ``opts[order[option_index]]``.

    Both sides MUST derive the permutation identically or recorded answers drift
    from what the user actually saw. Before this helper existed, serve shuffled
    the options in place while record indexed the raw stored order, so with 4
    options only the permutation's fixed points (~1 in 4) were recorded
    correctly — ~75% of answers recorded a different option than the user
    picked (regression introduced 2026-05-04; see ``test_answer_roundtrip``).

    The permutation is seeded by ``(question_number, question_text)`` so retries
    of the same question are stable while consecutive questions differ. It
    reproduces the exact ordering of ``random.Random(seed).shuffle(list)`` (same
    seed + same length ⇒ same swap sequence applied to any list), so callers may
    rely on ``[opts[i] for i in order]`` matching an in-place shuffle.
    """
    order = list(range(n))
    if n > 1:
        import hashlib as _hashlib
        import random as _random

        seed_src = f"{question_number or 0}::{question_text or ''}".encode("utf-8", errors="ignore")
        seed_int = int.from_bytes(_hashlib.sha256(seed_src).digest()[:8], "big", signed=False)
        _random.Random(seed_int).shuffle(order)
    return order


def _effective_max_questions(category: Any) -> int | None:
    """UX-2026-07-02 — the EFFECTIVE hard question cap for this quiz's topic.

    Single source of truth: the agent graph's ``_effective_depth_bounds`` — the
    exact bound the decision node uses to force-finish — so the FE's
    "Question N of up to M" denominator can never drift from when the quiz
    actually stops. Imported lazily (the graph module pulls in the LangGraph /
    tools stack) and fail-open: on any fault return None so /status still
    serves the question (the FE simply omits the denominator).
    """
    try:
        from app.agent.graph import _effective_depth_bounds

        _eff_min, eff_max = _effective_depth_bounds(
            category if isinstance(category, str) and category.strip() else None
        )
        return int(eff_max)
    except Exception:
        logger.debug("quiz.status.effective_max_questions.fail")
        return None


def _format_next_question(
    q_raw: Any,
    *,
    question_number: int | None = None,
    confidence: float | None = None,
    answered_count: int | None = None,
    max_questions: int | None = None,
) -> APIQuestion:
    """Extracts question text and options into API model.

    `question_number` is the 1-based ordinal of the question being served (i.e.
    `target_index + 1` in the calling context). It is surfaced to the FE so the
    quiz card can render "Question N" without doing its own counting.

    `confidence` is the agent's current best-guess confidence in [0, 1].
    Surfaced so the FE thinking-row can render "(N% confident)" alongside
    the rotating progress phrase (AC-UX-2026-05-08). Omitted (None) when
    the agent has not yet produced a confidence value.

    UX-2026-07-02 — `answered_count` (server-recorded answers so far) and
    `max_questions` (the topic-aware effective hard cap, see
    ``_effective_max_questions``) feed the FE closeness cue
    ("Question 7 of up to 12 — zeroing in"). Both optional; invalid values are
    dropped to None rather than crashing the serve path.
    """
    if hasattr(q_raw, "model_dump"):
        qd = q_raw.model_dump()
    elif isinstance(q_raw, dict):
        qd = dict(q_raw)
    else:
        qd = QuizQuestion.model_validate(q_raw).model_dump()

    text_val = qd.get("question_text", "") or qd.get("text", "")
    q_image = qd.get("image_url") or qd.get("imageUrl")
    q_image_alt = qd.get("image_alt") or qd.get("imageAlt")
    progress_phrase = qd.get("progress_phrase") or qd.get("progressPhrase")

    options_in = qd.get("options", []) or []
    options = []
    for o in options_in:
        if isinstance(o, dict):
            img = o.get("image_url") or o.get("imageUrl")
            img_alt = o.get("image_alt") or o.get("imageAlt")
            options.append(
                AnswerOption(text=str(o.get("text", "")), image_url=img, image_alt=img_alt)
            )
        else:
            options.append(AnswerOption(text=str(o), image_url=None))

    # Deterministic answer-option shuffle:
    # The LLM has a bias toward placing the most "natural" first answer in
    # slot A every time, which makes the quiz feel less random and rewards
    # users who always pick the first option. We re-order options with a
    # seed derived from (question_number, question_text) so the same question
    # always gets the same order on retries — but consecutive questions
    # surface different shapes.
    #
    # CRITICAL: the record path (`_validate_and_record_answer`) must de-map the
    # client's displayed index through the IDENTICAL permutation, so both sides
    # go through `_display_option_order` — never re-derive the seed inline.
    if len(options) > 1:
        order = _display_option_order(question_number, text_val, len(options))
        options = [options[i] for i in order]

    return APIQuestion(
        text=str(text_val),
        options=options,
        image_url=q_image,
        image_alt=q_image_alt if isinstance(q_image_alt, str) and q_image_alt.strip() else None,
        progress_phrase=progress_phrase if isinstance(progress_phrase, str) and progress_phrase.strip() else None,
        question_number=question_number if isinstance(question_number, int) and question_number > 0 else None,
        confidence=(
            float(confidence)
            if isinstance(confidence, (int, float)) and 0.0 < float(confidence) <= 1.0
            else None
        ),
        answered_count=(
            int(answered_count)
            if isinstance(answered_count, int) and answered_count >= 0
            else None
        ),
        max_questions=(
            int(max_questions)
            if isinstance(max_questions, int) and max_questions > 0
            else None
        ),
    )


def _questions_from_blob(blob: Any) -> list[dict[str, Any]]:
    """Extract a question list from a stored blob (``{"questions": [...]}`` or a
    bare list)."""
    if isinstance(blob, dict):
        return [q for q in (blob.get("questions") or []) if isinstance(q, dict)]
    if isinstance(blob, list):
        return [q for q in blob if isinstance(q, dict)]
    return []


async def _rehydrate_state_from_db(
    db_session: AsyncSession, quiz_id: uuid.UUID
) -> GraphState | None:
    """Rebuild a minimal GraphState from durable Postgres snapshots when the
    Redis live state has expired or been evicted (P1).

    /status previously read ONLY Redis (1h TTL, subject to maxmemory eviction),
    so a user who paused past the TTL — or any eviction — permanently lost a
    live (or even finished) quiz. All the data is already persisted (the same
    ``session_history`` row that /quiz/{id}/media serves, plus the
    ``session_questions`` blobs). Returns None only when the DB has no row.
    """
    from app.models.db import SessionHistory  # local: also imported lower for /media

    try:
        row = await db_session.get(SessionHistory, quiz_id)
    except Exception:
        logger.debug("quiz.status.rehydrate.db_fail", quiz_id=str(quiz_id))
        return None
    if row is None:
        return None

    sq = None
    try:
        sq = await SessionQuestionsRepository(db_session).get_for_session(quiz_id)
    except Exception:
        sq = None

    baseline_qs = _questions_from_blob(getattr(sq, "baseline_questions", None)) if sq else []
    adaptive_qs = _questions_from_blob(getattr(sq, "adaptive_questions", None)) if sq else []

    state: GraphState = _build_initial_graph_state(quiz_id, str(uuid.uuid4()), row.category or "")
    syn = row.category_synopsis if isinstance(row.category_synopsis, dict) else {}
    state["synopsis"] = dict(syn)
    state["generated_characters"] = [c for c in (row.character_set or []) if isinstance(c, dict)]
    state["generated_questions"] = [*baseline_qs, *adaptive_qs]
    state["baseline_count"] = len(baseline_qs)
    state["baseline_ready"] = bool(baseline_qs)
    state["ready_for_questions"] = True
    state["quiz_history"] = list(row.qa_history or [])
    if row.final_result:
        state["final_result"] = row.final_result
    return state


@router.get(
    "/quiz/status/{quiz_id}",
    response_model=QuizStatusResponse,
    summary="Poll for quiz status",
)
async def get_quiz_status(  # noqa: C901 — linear status orchestrator (final/next/processing + rehydrate)
    quiz_id: uuid.UUID,
    redis_client: Annotated[Any, Depends(get_redis_client)],
    db_session: Annotated[AsyncSession, Depends(get_db_session)],
    known_questions_count: Annotated[int, Query(ge=0, description="Client's known question count")] = 0,
):
    """
    Returns the next question if available, the final result if finished,
    or 'processing' if the agent is still working.
    """
    cache_repo = CacheRepository(redis_client)
    # Hitlist #11 (2026-06-30) — lightweight cache-hit read. The status poll
    # needs only ~4 scalar fields + the single unseen question, so we extract
    # them via a raw json.loads instead of re-validating + model_dump()-ing the
    # whole graph state on every (1–5s) poll. The values are identical to what
    # the validated state would yield (same JSON round-trip), so the response
    # schema/content is byte-for-byte unchanged.
    snapshot = await cache_repo.get_quiz_status_snapshot(quiz_id)

    if snapshot is None:
        # P1 — Redis live state expired/evicted (or unparsable): rebuild from
        # Postgres instead of permanently losing the quiz. (No DB connection is
        # taken on the hot cache-hit path — SQLAlchemy connects lazily on first
        # query.)
        rehydrated = await _rehydrate_state_from_db(db_session, quiz_id)
        if rehydrated is None:
            raise NotFoundError("Quiz session not found.")
        try:
            await cache_repo.save_quiz_state(rehydrated)  # re-prime so next poll hits cache
        except Exception:
            logger.debug("quiz.status.rehydrate.reprime_failed", quiz_id=str(quiz_id))
        logger.info("quiz.status.rehydrated_from_db", quiz_id=str(quiz_id))
        # Mirror the snapshot shape from the rehydrated full state so the rest
        # of the orchestrator reads one set of fields regardless of source.
        trace_id_val = rehydrated.get("trace_id")
        final_result_val: Any = rehydrated.get("final_result")
        generated: list[Any] = rehydrated.get("generated_questions", []) or []
        answered_idx = len(rehydrated.get("quiz_history") or [])
        current_confidence_val = rehydrated.get("current_confidence")
        last_served_index_val = rehydrated.get("last_served_index")
        category_val = rehydrated.get("category")
    else:
        trace_id_val = snapshot.trace_id
        final_result_val = snapshot.final_result
        generated = snapshot.generated_questions or []
        answered_idx = snapshot.quiz_history_len
        current_confidence_val = snapshot.current_confidence
        last_served_index_val = snapshot.last_served_index
        category_val = snapshot.category
    structlog.contextvars.bind_contextvars(trace_id=trace_id_val)

    # 1. Check Final Result
    if final_result_val:
        try:
            result = FinalResult.model_validate(final_result_val)
            logger.info("Quiz finished; returning final result", quiz_id=str(quiz_id))
            structlog.contextvars.clear_contextvars()
            return QuizStatusResult(status="finished", type="result", data=result)
        except Exception as e:
            # Hitlist #2 (2026-06-30) — DEGRADE, don't 500-forever. The previous
            # behaviour re-raised QF_MALFORMED_RESULT (500) on EVERY poll: the
            # cache miss-path rehydrates the SAME bad blob from Postgres, and the
            # recovery sweeper sees a truthy final_result and marks the job
            # succeeded, so nothing ever repairs it — a permanent 500 loop.
            #
            # Instead we (1) mark the durable job FAILED so subsequent polls take
            # the fatal-fast 422 branch the FE handles (not a 500), and (2)
            # best-effort CLEAR the corrupt blob from cache so the next poll falls
            # through to that job-status check cleanly instead of re-failing here.
            # Then return the 422 NOW (this poll) rather than a 500.
            logger.error(
                "Malformed final_result in state; degrading to terminal-failed",
                quiz_id=str(quiz_id),
                error=str(e),
                exc_info=True,
            )
            try:
                await QuizJobRepository(db_session).mark_failed(
                    quiz_id, error="malformed final_result"
                )
                await db_session.commit()
            except Exception:
                logger.debug(
                    "quiz.status.malformed_result.mark_failed_failed",
                    quiz_id=str(quiz_id),
                )
                try:
                    await db_session.rollback()
                except Exception:
                    pass
            try:
                await cache_repo.clear_final_result(quiz_id)
            except Exception:
                logger.debug(
                    "quiz.status.malformed_result.clear_failed", quiz_id=str(quiz_id)
                )
            structlog.contextvars.clear_contextvars()
            # Fatal-fast 422 (same code the failed-job branch returns), NOT a 500.
            raise coded_http_exception(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="This quiz could not be completed. Please start a new quiz.",
                code=QF_AGENT_FAILED,
            ) from e

    # 2. Check Next Question
    #
    # Deep-review #6 (BE half, 2026-07-02): serve strictly from the server's
    # own answer count. The next question a user must see is ALWAYS index
    # len(quiz_history) — in every legitimate flow the client's
    # known_questions_count equals answered_idx by the time a new question is
    # wanted, so this is behavior-identical on the happy path. The one case
    # where they diverge (client claims to "know" a question it hasn't
    # answered, e.g. a poll racing an on-screen unanswered question) used to
    # make max() SKIP that question and silently misattribute the next click.
    # Trusting only answered_idx re-serves the current unanswered question
    # instead — idempotent for the FE (same ordinal → same content).
    # `known_questions_count` remains in the API for back-compat but is no
    # longer trusted for serve decisions.
    _ = known_questions_count  # accepted, deliberately not trusted (see above)
    target_index = answered_idx

    if len(generated) <= target_index:
        # No result and no unseen question -> the agent is (or should be) still
        # working. Consult the durable job row (audit P1): a deterministically
        # FAILED/exhausted run would otherwise poll 'processing' forever (until
        # the FE's ~60s timeout) because the cache never gains a result. Surface
        # a terminal 4xx the FE treats as fatal-fast so the user isn't dead-ended
        # on a spinner. A 'running'/'succeeded'/absent row keeps the normal
        # processing flow. Best-effort: a job-table fault falls through to
        # 'processing' (never blocks the happy path).
        try:
            job_status = await QuizJobRepository(db_session).get_status(quiz_id)
        except Exception:
            job_status = None
        if job_status == "failed":
            logger.info(
                "quiz.status.job_failed_terminal", quiz_id=str(quiz_id)
            )
            structlog.contextvars.clear_contextvars()
            raise coded_http_exception(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="This quiz could not be completed. Please start a new quiz.",
                code=QF_AGENT_FAILED,
            )
        structlog.contextvars.clear_contextvars()
        return ProcessingResponse(status="processing", quiz_id=quiz_id)

    # 3. Format Response
    try:
        # AC-UX-2026-05-08 — surface agent confidence to the FE so the
        # thinking-row can render "(N% confident)" alongside the
        # progress phrase. `current_confidence` is set by the decision
        # node and may be None on early questions.
        raw_conf = current_confidence_val
        conf_val: float | None
        if isinstance(raw_conf, (int, float)):
            conf_val = float(raw_conf)
            if conf_val > 1.0:
                # Normalise legacy 0–100 scores.
                conf_val = min(1.0, conf_val / 100.0)
        else:
            conf_val = None
        new_question_api = _format_next_question(
            generated[target_index],
            question_number=target_index + 1,
            confidence=conf_val,
            # UX-2026-07-02 — real progress for the FE closeness cue: how many
            # answers the server has recorded, and the topic-aware hard cap
            # this quiz will never exceed (the agent may finish EARLIER on
            # confidence, hence the FE's "of up to" phrasing).
            answered_count=target_index,
            max_questions=_effective_max_questions(category_val),
        )
    except Exception as e:
        logger.error("Failed to validate question model", quiz_id=str(quiz_id), error=str(e), exc_info=True)
        structlog.contextvars.clear_contextvars()
        raise coded_http_exception(
            status_code=500,
            detail="Malformed question data.",
            code=QF_MALFORMED_QUESTION,
        ) from e

    # Update last served pointer atomically — but ONLY when it actually
    # advances (P1 perf). During the window where a question is ready but the
    # client hasn't yet bumped known_questions_count, the SAME target_index is
    # re-served on every poll (every 1-5s per user). Re-writing it each time is
    # pure write-amplification — update_quiz_state_atomically does
    # WATCH/GET/MULTI/SET plus full-state validate+dump ~3x — on the hottest
    # endpoint, and contends with the background agent's WATCH. The field is
    # only persisted, never read for control logic, so skipping the no-op write
    # is safe. The atomic merge is still used (never a full SET) so a concurrent
    # agent write is preserved.
    try:
        if last_served_index_val != target_index:
            await cache_repo.update_quiz_state_atomically(
                quiz_id, {"last_served_index": target_index}
            )
    except Exception:
        logger.debug("Non-fatal: failed to persist last_served_index", quiz_id=str(quiz_id))

    logger.info("Returning next unseen question to client", quiz_id=str(quiz_id), index=target_index)
    structlog.contextvars.clear_contextvars()
    return QuizStatusQuestion(status="active", type="question", data=new_question_api)


# ---------------------------------------------------------------------------
# Async-image snapshot (AC-MEDIA-1..6)
# ---------------------------------------------------------------------------
# Synopsis, character, and final-result images are generated by FAL in
# background tasks scheduled from /quiz/start (and the finalisation path).
# Those tasks persist URLs into Postgres only — they never write to the agent
# state in Redis. This endpoint exposes a lightweight read-through snapshot of
# whatever has been persisted so the FE can poll and surface images as they
# become available, without ever blocking the user-visible quiz flow.

from app.models.db import SessionHistory  # noqa: E402


@router.get(
    "/quiz/{quiz_id}/media",
    response_model=QuizMediaResponse,
    summary="Snapshot of asynchronously-generated images for a quiz session",
)
async def get_quiz_media(
    quiz_id: uuid.UUID,
    db_session: Annotated[AsyncSession, Depends(get_db_session)],
) -> QuizMediaResponse:
    """Return whatever image URLs have been persisted for this session so far.

    All fields default to ``null`` / ``[]`` when no row exists yet (e.g. the
    background persister hasn't run) so the FE can poll this endpoint cheaply
    from the moment the synopsis renders without coordinating with the agent.

    Behaviour (AC-MEDIA-*):
    - AC-MEDIA-1: Always returns 200 with the response shape, even when no
      ``session_history`` row exists yet.
    - AC-MEDIA-2: ``synopsisImageUrl`` is sourced from
      ``session_history.category_synopsis->>'image_url'``.
    - AC-MEDIA-3: ``characters[]`` is derived from
      ``session_history.character_set`` (preserves order, deduplicates by name,
      includes entries even when their ``imageUrl`` is still ``null``).
    - AC-MEDIA-4: ``resultImageUrl`` is sourced from
      ``session_history.final_result->>'image_url'`` (``null`` until the quiz
      has been finalised and the result image has been persisted).
    - AC-MEDIA-5: Endpoint is read-only — never writes, never schedules work,
      never raises 5xx for empty / missing rows.
    - AC-MEDIA-6: No Turnstile, no session lock, no Redis access — purely a
      DB read by primary key.
    """
    try:
        row: SessionHistory | None = await db_session.get(SessionHistory, quiz_id)
    except Exception as e:
        logger.warning(
            "quiz.media.db.fail",
            quiz_id=str(quiz_id),
            error=str(e),
        )
        # Fail soft — return an empty snapshot so the FE keeps polling.
        return QuizMediaResponse(quiz_id=quiz_id)

    if row is None:
        return QuizMediaResponse(quiz_id=quiz_id)

    syn = row.category_synopsis or {}
    res = row.final_result or {}
    cset = row.character_set or []

    seen: set[str] = set()
    chars: list[CharacterImage] = []
    for elem in cset:
        if not isinstance(elem, dict):
            continue
        name = (elem.get("name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        url = elem.get("image_url") or elem.get("imageUrl")
        chars.append(CharacterImage(name=name, image_url=url or None))

    return QuizMediaResponse(
        quiz_id=quiz_id,
        synopsis_image_url=(syn.get("image_url") if isinstance(syn, dict) else None) or None,
        result_image_url=(res.get("image_url") if isinstance(res, dict) else None) or None,
        characters=chars,
    )
