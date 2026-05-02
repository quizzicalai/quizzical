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
    Response,
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
from app.core.errors import NotFoundError, SessionBusyError
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
from app.services import image_pipeline as _image_pipeline

# NEW: use repositories & association table for persistence
from app.services.database import (
    CharacterRepository,
    SessionQuestionsRepository,
    SessionRepository,
)
from app.services.redis_cache import CacheRepository

router = APIRouter()
logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Local utilities
# ---------------------------------------------------------------------------

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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Agent service is not available.",
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

async def _save_final_state_to_cache(cache_repo: CacheRepository, session_id: str, state: GraphState) -> None:
    try:
        t_save = time.perf_counter()
        await cache_repo.save_quiz_state(state)
        save_ms = round((time.perf_counter() - t_save) * 1000, 1)
        logger.info(
            "Final agent state saved to cache",
            quiz_id=session_id,
            save_duration_ms=save_ms,
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
                        analysis=state.get("analysis") or {},
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

    try:
        config = {"configurable": {"thread_id": session_id_str}}
        logger.debug("Agent background stream starting", quiz_id=session_id_str)

        async for _ in agent_graph.astream(state_dict, config=config):  # type: ignore[attr-defined]
            steps += 1

        final_state_snapshot = await agent_graph.aget_state(config)  # type: ignore[attr-defined]
        final_state = final_state_snapshot.values

        duration_ms = round((time.perf_counter() - t_start) * 1000, 1)
        logger.info(
            "Agent graph finished in background",
            quiz_id=session_id_str,
            steps=steps,
            duration_ms=duration_ms,
        )

    except Exception as e:
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
        # Persist results
        await _save_final_state_to_cache(cache_repo, session_id_str, final_state)

        if session_id and isinstance(session_id, uuid.UUID):
            await _persist_baseline_questions(session_id, session_id_str, final_state)
            await _persist_adaptive_and_final(session_id, session_id_str, final_state)

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
    quiz_id: uuid.UUID, trace_id: str, category: str
) -> GraphState:
    """Construct the initial GraphState dict for a new quiz session."""
    return {
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
        analysis_payload = state.get("analysis") or {}
        syn_obj = state.get("synopsis")
        chars_obj = list(state.get("generated_characters") or [])
        if syn_obj is not None:
            background_tasks.add_task(
                _image_pipeline.generate_synopsis_image,
                session_id=quiz_id,
                synopsis=syn_obj,
                category=category,
                analysis=analysis_payload,
            )
        if chars_obj:
            background_tasks.add_task(
                _image_pipeline.generate_character_images,
                session_id=quiz_id,
                characters=chars_obj,
                category=category,
                analysis=analysis_payload,
            )
    except Exception:
        logger.exception("Failed to schedule image jobs", quiz_id=str(quiz_id))


async def _short_circuit_from_pack(
    db_session: AsyncSession,
    *,
    cache_repo: CacheRepository,
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

    from app.services.precompute.hydrator import hydrate_pack as _hydrate_pack

    hydrated = await _hydrate_pack(db_session, pack_id=pack_id)
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
    )
    return _build_start_response(quiz_id, state)


async def _hydrate_resolved_pack(
    db_session: AsyncSession,
    *,
    resolution: Any,
) -> Any:
    """Build a `ResolvedPack` for the resolver-returned topic + pack ids.

    Used as the `fill_fn` for `cache.get_or_fill` on a /quiz/start cache
    miss. Returns None when the pack row is missing (e.g. concurrent
    quarantine) — the caller treats None as "no pack to preload" and
    proceeds to the normal generation path without a Link header.
    """
    from sqlalchemy import select  # local import to keep top-of-file lean

    from app.models.db import TopicPack
    from app.services.precompute.cache import ResolvedPack

    pack_id = getattr(resolution, "pack_id", None)
    topic_id = getattr(resolution, "topic_id", None)
    if pack_id is None or topic_id is None:
        return None
    try:
        row = (
            await db_session.execute(
                select(TopicPack).where(TopicPack.id == pack_id)
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        # Pack→media linkage lands in Phase 5 (image storage 0→0.5). Until
        # then we cache the structural ids only and emit no Link header
        # (an empty `storage_uris` tuple yields an empty header that the
        # caller skips).
        return ResolvedPack(
            topic_id=str(topic_id),
            pack_id=str(row.id),
            version=int(getattr(row, "version", 0) or 0),
            synopsis_id=str(row.synopsis_id),
            character_set_id=str(row.character_set_id),
            baseline_question_set_id=str(row.baseline_question_set_id),
            storage_uris=(),
        )
    except Exception:
        logger.exception("precompute.cache.hydrate_failed", pack_id=str(pack_id))
        return None


@router.post(
    "/quiz/start",
    response_model=FrontendStartQuizResponse,
    summary="Start a new quiz session",
    status_code=status.HTTP_201_CREATED,
)
async def start_quiz(  # noqa: C901 — orchestrator: budget/lookup/cache/agent branches are inherent
    request: StartQuizRequest,
    response: Response,
    background_tasks: BackgroundTasks,
    agent_graph: Annotated[object, Depends(get_agent_graph)],
    redis_client: Annotated[Any, Depends(get_redis_client)],
    db_session: Annotated[AsyncSession, Depends(get_db_session)],
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

    structlog.contextvars.bind_contextvars(trace_id=trace_id)
    logger.info(
        "Starting new quiz session",
        quiz_id=str(quiz_id),
        category=request.category,
        env=settings.app.environment,
    )

    # §21 Phase 2 — Read-path lookup shim. When `precompute.enabled=False`
    # (the default through Phase 5 per Universal-G5) this is a no-op and the
    # response below is byte-for-byte identical to the pre-§21 behaviour.
    # Phase 3 (added) — On a HIT with a fully-baked pack we skip the agent
    # entirely (see `_short_circuit_from_pack` further down). Image jobs are
    # still scheduled in the background via the FAL pipeline.
    resolution = None
    if getattr(getattr(settings, "precompute", None), "enabled", False):
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
            # §21 Phase 4 — On HIT, hydrate the resolved pack via the
            # SETNX-guarded Redis cache (`AC-PRECOMP-PERF-2`) and attach
            # a `Link: rel=preload` header so the client can warm media
            # asset fetches in parallel with synopsis rendering
            # (`AC-PRECOMP-PERF-3`). Failure here is advisory — never
            # break /quiz/start.
            if resolution is not None:
                from app.services.precompute import cache as _pack_cache

                async def _fill_from_db() -> _pack_cache.ResolvedPack | None:
                    return await _hydrate_resolved_pack(
                        db_session, resolution=resolution
                    )

                pack = await _pack_cache.get_or_fill(
                    redis_client,
                    str(resolution.topic_id),
                    _fill_from_db,
                )
                if pack is not None and pack.storage_uris:
                    link = _pack_cache.build_link_header(pack.storage_uris)
                    if link:
                        response.headers["Link"] = link
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
                background_tasks=background_tasks,
                quiz_id=quiz_id,
                trace_id=trace_id,
                category=request.category,
                pack_id=getattr(resolution, "pack_id", None),
            )
            if short is not None:
                return short
        except Exception:
            # Fall back to the live agent path on any unexpected error so
            # users still get an experience even if the precompute layer is
            # misbehaving.
            logger.exception(
                "precompute.start.short_circuit_error", quiz_id=str(quiz_id)
            )

    # Initial graph state
    initial_state: GraphState = _build_initial_graph_state(quiz_id, trace_id, request.category)

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
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="The AI agent failed to generate a quiz synopsis. Please try a different category.",
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
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Our crystal ball is a bit cloudy and we couldn't conjure up your quiz in time. Please try another category!",
        ) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to start quiz session", quiz_id=str(quiz_id), error=str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="An unexpected error occurred while starting the quiz. Our wizards have been notified.",
        ) from e
    finally:
        structlog.contextvars.clear_contextvars()


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
        current_state = await cache_repo.get_quiz_state(request.quiz_id)
        if not current_state:
            logger.warning("Quiz session not found on proceed", quiz_id=quiz_id_str)
            raise NotFoundError("Quiz session not found.")

        # Flip the questions gate and persist snapshot BEFORE scheduling background work
        current_state_dict: GraphState = current_state.model_dump()
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

def _validate_and_record_answer(state_dict: dict, request: NextQuestionRequest) -> tuple[list[dict], list[Any]]:
    """Validates the user's answer index and updates history."""
    history = list(state_dict.get("quiz_history") or [])
    expected_index = len(history)
    q_index = request.question_index

    if q_index < expected_index:
        # Duplicate logic handled by caller
        raise ValueError("DUPLICATE")

    if q_index > expected_index:
        raise HTTPException(status_code=409, detail="Stale or out-of-order answer.")

    server_qs = state_dict.get("generated_questions") or []
    if q_index < 0 or q_index >= len(server_qs):
        raise HTTPException(status_code=400, detail="question_index out of range.")

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

    if option_index is not None:
        if option_index < 0 or option_index >= len(opts):
            raise HTTPException(status_code=400, detail="option_index out of range.")
        ans_text = str(opts[option_index].get("text") or ans_text)

    new_history = [*history, {
        "question_index": q_index,
        "question_text": qd.get("question_text", ""),
        "answer_text": ans_text,
        "option_index": option_index,
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
        current_state = await cache_repo.get_quiz_state(request.quiz_id)
        if not current_state:
            raise NotFoundError("Quiz session not found.")

        state_dict: GraphState = current_state.model_dump()
        structlog.contextvars.bind_contextvars(trace_id=state_dict.get("trace_id"))

        # Validate and update state
        try:
            new_history, new_messages = _validate_and_record_answer(state_dict, request)
        except ValueError as e:
            if str(e) == "DUPLICATE":
                logger.info("Duplicate answer received", quiz_id=quiz_id_str)
                structlog.contextvars.clear_contextvars()
                return ProcessingResponse(status="processing", quiz_id=request.quiz_id)
            raise HTTPException(status_code=400, detail="Invalid answer state.") from e
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Failed to record answer", quiz_id=quiz_id_str, error=str(e), exc_info=True)
            raise HTTPException(status_code=400, detail="Invalid answer payload.") from e

        # Persist atomic update
        updated_state = await cache_repo.update_quiz_state_atomically(request.quiz_id, {
            "quiz_history": new_history,
            "messages": new_messages,
            "ready_for_questions": True,
        })
        if updated_state is None:
            raise HTTPException(status_code=409, detail="Please retry answer submission.")

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
            background_tasks.add_task(run_agent_in_background, updated_state_dict, redis_client, agent_graph)
            logger.info("Background task scheduled", quiz_id=quiz_id_str)

        structlog.contextvars.clear_contextvars()
        return ProcessingResponse(status="processing", quiz_id=request.quiz_id)
    finally:
        await _sl.release(redis_client, quiz_id_str, _lock_token)


# ---------------------------------------------------------------------------
# Quiz Status Helpers (Extracted to fix C901)
# ---------------------------------------------------------------------------

def _format_next_question(q_raw: Any) -> APIQuestion:
    """Extracts question text and options into API model."""
    if hasattr(q_raw, "model_dump"):
        qd = q_raw.model_dump()
    elif isinstance(q_raw, dict):
        qd = dict(q_raw)
    else:
        qd = QuizQuestion.model_validate(q_raw).model_dump()

    text_val = qd.get("question_text", "") or qd.get("text", "")
    q_image = qd.get("image_url") or qd.get("imageUrl")

    options_in = qd.get("options", []) or []
    options = []
    for o in options_in:
        if isinstance(o, dict):
            img = o.get("image_url") or o.get("imageUrl")
            options.append(AnswerOption(text=str(o.get("text", "")), image_url=img))
        else:
            options.append(AnswerOption(text=str(o), image_url=None))

    return APIQuestion(text=str(text_val), options=options, image_url=q_image)


@router.get(
    "/quiz/status/{quiz_id}",
    response_model=QuizStatusResponse,
    summary="Poll for quiz status",
)
async def get_quiz_status(
    quiz_id: uuid.UUID,
    redis_client: Annotated[Any, Depends(get_redis_client)],
    known_questions_count: Annotated[int, Query(ge=0, description="Client's known question count")] = 0,
):
    """
    Returns the next question if available, the final result if finished,
    or 'processing' if the agent is still working.
    """
    cache_repo = CacheRepository(redis_client)
    state_model = await cache_repo.get_quiz_state(quiz_id)

    if not state_model:
        raise NotFoundError("Quiz session not found.")

    state: GraphState = state_model.model_dump()
    structlog.contextvars.bind_contextvars(trace_id=state.get("trace_id"))

    # 1. Check Final Result
    if state.get("final_result"):
        try:
            result = FinalResult.model_validate(state["final_result"])
            logger.info("Quiz finished; returning final result", quiz_id=str(quiz_id))
            structlog.contextvars.clear_contextvars()
            return QuizStatusResult(status="finished", type="result", data=result)
        except Exception as e:
            logger.error("Malformed final_result in state", quiz_id=str(quiz_id), error=str(e), exc_info=True)
            raise HTTPException(status_code=500, detail="Malformed result data.") from e

    # 2. Check Next Question
    generated: list[Any] = state.get("generated_questions", []) or []
    answered_idx = len(state.get("quiz_history") or [])
    target_index = max(answered_idx, known_questions_count)

    if len(generated) <= target_index:
        structlog.contextvars.clear_contextvars()
        return ProcessingResponse(status="processing", quiz_id=quiz_id)

    # 3. Format Response
    try:
        new_question_api = _format_next_question(generated[target_index])
    except Exception as e:
        logger.error("Failed to validate question model", quiz_id=str(quiz_id), error=str(e), exc_info=True)
        structlog.contextvars.clear_contextvars()
        raise HTTPException(status_code=500, detail="Malformed question data.") from e

    # Update last served pointer atomically: a background agent task may be
    # writing other fields concurrently, so a full save_quiz_state SET would
    # clobber its progress. update_quiz_state_atomically uses Redis WATCH/MULTI
    # to merge only this single field.
    try:
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
