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
from typing import Any, Dict, Optional, List

import structlog
from fastapi.encoders import jsonable_encoder  # NEW
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    status,
)
from langchain_core.messages import HumanMessage
from pydantic import ValidationError
from sqlalchemy import text  # kept to avoid changing import surface; not used for writes now
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.api.dependencies import (
    get_db_session,
    get_redis_client,
    verify_turnstile,
)
from app.core.config import settings
from app.models.api import (
    CharactersPayload,
    FrontendStartQuizResponse,
    NextQuestionRequest,
    ProcessingResponse,
    Question as APIQuestion,
    AnswerOption,
    QuizStatusResponse,
    StartQuizPayload,
    StartQuizRequest,
    ProceedRequest,
    Synopsis as APISynopsis,
    QuizQuestion as APIQuizQuestion,
    FinalResult,
    QuizStatusQuestion,
    QuizStatusResult,
)
from app.services.redis_cache import CacheRepository
from app.agent.schemas import AgentGraphStateModel
from app.agent.state import GraphState
from app.agent.schemas import QuizQuestion  # noqa: F401 (type clarity)

# NEW: use repositories & association table for persistence
from app.services.database import SessionRepository, CharacterRepository, SessionQuestionsRepository
from app.models.db import character_session_map

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


def _safe_len(obj) -> Optional[int]:
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


def _as_payload_dict(obj: Any, variant: str) -> Dict[str, Any]:
    """Normalize for StartQuizPayload discriminated union."""
    if hasattr(obj, "model_dump"):
        base = obj.model_dump()
    elif isinstance(obj, dict):
        base = dict(obj)
    else:
        if variant == "synopsis":
            validated = APISynopsis.model_validate(obj)
            base = validated.model_dump()
        else:
            validated = APIQuizQuestion.model_validate(obj)
            base = validated.model_dump()
    base["type"] = variant
    return base


def _character_to_dict(obj: Any) -> Dict[str, Any]:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if isinstance(obj, dict):
        return dict(obj)
    return {
        "name": getattr(obj, "name", ""),
        "short_description": getattr(obj, "short_description", ""),
        "profile_text": getattr(obj, "profile_text", ""),
        "image_url": getattr(obj, "image_url", None),
    }


def _to_state_dict(obj: Any) -> Dict[str, Any]:
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            pass
    if isinstance(obj, dict):
        return dict(obj)
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

def _serialize_synopsis(obj: Any) -> Dict[str, Any]:
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            pass
    if isinstance(obj, dict):
        return dict(obj)
    return {
        "title": getattr(obj, "title", None) or "",
        "summary": getattr(obj, "summary", None) or "",
    }


def _bootstrap_transcript(category: str, synopsis_dict: Dict[str, Any]) -> List[Dict[str, Any]]:
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
    characters: List[Any],
) -> None:
    """
    Upsert characters (unique by name) and link them to this session via M:N table.
    Does NOT touch session transcript / synopsis.
    """
    if not characters:
        return

    char_repo = CharacterRepository(db)

    # Upsert characters and collect their IDs
    ids: List[uuid.UUID] = []
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
    characters: List[Any],
    write_session_row: bool = True,
    agent_plan: Optional[Dict[str, Any]] = None,
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
        messages_count=_safe_len(state_dict.get("messages")),
        generated_questions_count=_safe_len(state_dict.get("generated_questions")),
        generated_characters_count=_safe_len(state_dict.get("generated_characters")),
        ready_for_questions=bool(state_dict.get("ready_for_questions")),
    )
    if _is_local_env():
        try:
            logger.debug(
                "Type check (pre-stream)",
                char_types=[type(c).__name__ for c in (state_dict.get("generated_characters") or [])],
                q_types=[type(q).__name__ for q in (state_dict.get("generated_questions") or [])],
                synopsis_type=type(state_dict.get("synopsis")).__name__
                if state_dict.get("synopsis") is not None
                else None,
            )
        except Exception:
            pass

    final_state: GraphState = state_dict
    steps = 0
    t_start = time.perf_counter()

    try:
        config = {"configurable": {"thread_id": session_id_str}}

        logger.debug(
            "Agent background stream starting",
            quiz_id=session_id_str,
            config_keys=list(config.get("configurable", {}).keys()),
        )
        async for _ in agent_graph.astream(state_dict, config=config):  # type: ignore[attr-defined]
            steps += 1
            if steps % 5 == 0 and _is_local_env():
                logger.debug(
                    "Agent background progress tick",
                    quiz_id=session_id_str,
                    steps=steps,
                )

        final_state_snapshot = await agent_graph.aget_state(config)  # type: ignore[attr-defined]
        final_state = final_state_snapshot.values

        duration_ms = round((time.perf_counter() - t_start) * 1000, 1)
        logger.info(
            "Agent graph finished in background",
            quiz_id=session_id_str,
            steps=steps,
            duration_ms=duration_ms,
            final_keys=list(final_state.keys()) if isinstance(final_state, dict) else None,
            final_messages_count=_safe_len(final_state.get("messages")) if isinstance(final_state, dict) else None,
            final_questions_count=_safe_len(final_state.get("generated_questions")) if isinstance(final_state, dict) else None,
        )

    except Exception as e:
        details = _exc_details()
        logger.error(
            "Agent graph failed in background",
            quiz_id=session_id_str,
            error=str(e),
            **details,
            exc_info=True,
        )
        try:
            if isinstance(final_state, dict) and "messages" in final_state:
                final_state["messages"].append(HumanMessage(content=f"Agent failed with error: {e}"))
        except Exception:
            logger.debug("Could not append error message to final_state.messages", quiz_id=session_id_str)
    finally:
        try:
            t_save = time.perf_counter()
            await cache_repo.save_quiz_state(final_state)
            save_ms = round((time.perf_counter() - t_save) * 1000, 1)
            logger.info(
                "Final agent state saved to cache",
                quiz_id=session_id_str,
                save_duration_ms=save_ms,
            )
        except Exception as e:
            logger.error(
                "Failed to save final agent state to cache",
                quiz_id=session_id_str,
                error=str(e),
                **_exc_details(),
                exc_info=True,
            )
        # --- NEW: persist baseline questions blob to DB (idempotent) ---
        try:
            if isinstance(final_state, dict) and final_state.get("baseline_ready"):
                baseline_count = int(final_state.get("baseline_count") or 0)
                if baseline_count > 0:
                    agen = get_db_session()  # borrow an AsyncSession from the dependency
                    db = await agen.__anext__()
                    try:
                        sq_repo = SessionQuestionsRepository(db)
                        # Skip if baseline already exists
                        already = await sq_repo.baseline_exists(session_id)
                        if not already:
                            baseline_blob = {
                                "questions": (final_state.get("generated_questions") or [])[:baseline_count]
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

        # --- NEW: persist adaptive questions blob & final result + qa_history ---
        try:
            agen = get_db_session()
            db = await agen.__anext__()
            try:
                # Repos
                sq_repo = SessionQuestionsRepository(db)
                sess_repo = SessionRepository(db)

                # 1) Adaptive questions snapshot (idempotent overwrite)
                if isinstance(final_state, dict) and final_state.get("baseline_ready"):
                    baseline_count = int(final_state.get("baseline_count") or 0)
                    all_qs = list(final_state.get("generated_questions") or [])
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
                if isinstance(final_state, dict) and final_state.get("final_result"):
                    # Ensure JSON-serializable payloads (FinalResult may be a Pydantic model)
                    fr_payload = jsonable_encoder(final_state.get("final_result"))  # <-- FIX
                    qa_hist_payload = jsonable_encoder(list(final_state.get("quiz_history") or []))
                    await sess_repo.mark_completed(
                        session_id=session_id,
                        final_result=fr_payload,
                        qa_history=qa_hist_payload,
                    )
                await db.commit()
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

        structlog.contextvars.clear_contextvars()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post(
    "/quiz/start",
    response_model=FrontendStartQuizResponse,
    summary="Start a new quiz session",
    status_code=status.HTTP_201_CREATED,
)
async def start_quiz(
    request: StartQuizRequest,
    agent_graph: object = Depends(get_agent_graph),
    redis_client: Any = Depends(get_redis_client),
    db_session: AsyncSession = Depends(get_db_session),
    turnstile_verified: bool = Depends(verify_turnstile),
):
    """
    Starts a quiz session and (within a strict time budget) waits for:
      1) Generated synopsis
      2) Attempts to stream initial character set within a separate budget

    Baseline questions are NOT generated here.
    """
    quiz_id = uuid.uuid4()
    trace_id = str(uuid.uuid4())
    cache_repo = CacheRepository(redis_client)

    structlog.contextvars.bind_contextvars(trace_id=trace_id)
    logger.info(
        "Starting new quiz session",
        quiz_id=str(quiz_id),
        category=request.category,
        turnstile_verified=bool(turnstile_verified),
        env=settings.app.environment,
    )

    if _is_local_env():
        try:
            tool_models = {k: v.model for k, v in (settings.llm_tools or {}).items()}
        except Exception:
            tool_models = {}
        logger.debug(
            "LLM configuration snapshot (local only)",
            llm_tool_models=tool_models,
            prompt_keys=list((settings.llm_prompts or {}).keys()),
        )

    # Initial graph state matches agent expectations
    initial_state: GraphState = {
        "session_id": quiz_id,
        "trace_id": trace_id,
        "category": request.category,
        "messages": [HumanMessage(content=request.category)],
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

    logger.debug(
        "Prepared initial graph state",
        state_keys=list(initial_state.keys()),
        messages_count=_safe_len(initial_state.get("messages")),
        questions_count=_safe_len(initial_state.get("generated_questions")),
        characters_count=_safe_len(initial_state.get("generated_characters")),
        ready_for_questions=bool(initial_state.get("ready_for_questions")),
    )

    # Time budgets (configurable with safe fallbacks).
    try:
        FIRST_STEP_TIMEOUT_S = float(getattr(getattr(settings, "quiz", None), "first_step_timeout_s", 30.0))
    except Exception:
        FIRST_STEP_TIMEOUT_S = 30.0
    try:
        STREAM_BUDGET_S = float(getattr(getattr(settings, "quiz", None), "stream_budget_s", 30.0))
    except Exception:
        STREAM_BUDGET_S = 30.0

    try:
        # --- Step 1: get synopsis quickly
        config = {"configurable": {"thread_id": str(quiz_id)}}

        logger.debug(
            "Invoking agent graph (initial step)",
            quiz_id=str(quiz_id),
            timeout_seconds=FIRST_STEP_TIMEOUT_S,
            config_keys=list(config.get("configurable", {}).keys()),
        )
        t0 = time.perf_counter()
        await asyncio.wait_for(  # type: ignore[attr-defined]
            agent_graph.ainvoke(initial_state, config),
            timeout=FIRST_STEP_TIMEOUT_S,
        )
        state_snapshot = await agent_graph.aget_state(config)  # type: ignore[attr-defined]
        state_after_first: GraphState = state_snapshot.values

        invoke_ms = round((time.perf_counter() - t0) * 1000, 1)
        logger.info(
            "Agent initial step completed",
            quiz_id=str(quiz_id),
            duration_ms=invoke_ms,
            snapshot_received=bool(state_after_first),
        )

        # Require canonical key only
        synopsis_obj = state_after_first.get("synopsis")
        if not synopsis_obj:
            logger.error(
                "Agent failed to generate synopsis",
                quiz_id=str(quiz_id),
                initial_step_state_present=bool(state_after_first),
                has_synopsis=False,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="The AI agent failed to generate a quiz synopsis. Please try a different category.",
            )

        # Save snapshot (synopsis ready)
        await cache_repo.save_quiz_state(state_after_first)
        logger.debug("Saved state after synopsis", quiz_id=str(quiz_id))

        # ---- Minimal persistence: write session+synopsis (+agent_plan); characters added if present
        try:
            # Build agent_plan from planner outputs (prefer state key; else derive)
            ideal_archetypes = state_after_first.get("ideal_archetypes") or []
            plan_from_state = state_after_first.get("agent_plan") or {}
            if not plan_from_state:
                syn_dict = _serialize_synopsis(state_after_first.get("synopsis"))
                plan_from_state = {
                    "title": syn_dict.get("title", ""),
                    "synopsis": syn_dict.get("summary", ""),
                    "ideal_archetypes": list(ideal_archetypes),
                    # include if state carries one (safe no-op otherwise)
                    **({"ideal_count_hint": state_after_first.get("ideal_count_hint")}
                       if state_after_first.get("ideal_count_hint") is not None else {}),
                }

            await _persist_initial_snapshot(
                db_session,
                session_id=quiz_id,
                category=request.category,
                synopsis=state_after_first.get("synopsis"),
                characters=state_after_first.get("generated_characters") or [],
                write_session_row=True,
                agent_plan=plan_from_state,
            )
            logger.info("Initial session snapshot persisted", quiz_id=str(quiz_id))
        except Exception:
            logger.exception("Failed to persist initial session snapshot", quiz_id=str(quiz_id))

        # --- Step 2: stream until characters appear or we hit the budget
        have_characters = bool(state_after_first.get("generated_characters"))
        if not have_characters:
            t_stream_start = time.perf_counter()
            steps = 0
            async for _ in agent_graph.astream(state_after_first, config=config):  # type: ignore[attr-defined]
                steps += 1
                current = await agent_graph.aget_state(config)  # type: ignore[attr-defined]
                current_values: GraphState = current.values
                have_characters = bool(current_values.get("generated_characters"))
                if have_characters:
                    # Gate still closed; ensure it stays that way
                    current_values["ready_for_questions"] = False
                    await cache_repo.save_quiz_state(current_values)
                    logger.info(
                        "Characters generated during start",
                        quiz_id=str(quiz_id),
                        step=steps,
                        character_count=len(current_values.get("generated_characters", [])),
                    )
                    # Persist characters that appeared during streaming (no-op if already saved)
                    try:
                        await _persist_initial_snapshot(
                            db_session,
                            session_id=quiz_id,
                            category=request.category,
                            synopsis=state_after_first.get("synopsis"),
                            characters=current_values.get("generated_characters") or [],
                            write_session_row=False,   # don’t overwrite agent_plan with None
                            agent_plan=None,
                        )
                        logger.info("Characters persisted post-stream", quiz_id=str(quiz_id))
                    except Exception:
                        logger.exception("Failed to persist characters post-stream", quiz_id=str(quiz_id))

                    state_after_first = current_values
                    break
                if (time.perf_counter() - t_stream_start) >= STREAM_BUDGET_S:
                    logger.warning(
                        "Character generation exceeded time budget; returning synopsis-only",
                        quiz_id=str(quiz_id),
                    )
                    break

        # Build response payload(s) with explicit discriminator to satisfy the union
        try:
            payload_synopsis = state_after_first.get("synopsis")
            synopsis_data = _as_payload_dict(payload_synopsis, "synopsis")
            synopsis_payload = StartQuizPayload(type="synopsis", data=synopsis_data)
        except ValidationError as ve:
            logger.error(
                "Validation error building StartQuizPayload",
                quiz_id=str(quiz_id),
                errors=ve.errors(),
                exc_info=True,
            )
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=ve.errors())

        characters = state_after_first.get("generated_characters", []) or []
        characters_payload = None
        if characters:
            try:
                characters_payload = CharactersPayload(
                    data=[_character_to_dict(c) for c in characters]
                )
            except ValidationError as ve:
                # Don’t fail start; just omit characters if they’re malformed
                logger.error(
                    "Validation error building CharactersPayload",
                    quiz_id=str(quiz_id),
                    errors=ve.errors(),
                    exc_info=True,
                )
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

    except asyncio.TimeoutError:
        logger.warning(
            "Quiz start process timed out",
            quiz_id=str(quiz_id),
            category=request.category,
        )
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Our crystal ball is a bit cloudy and we couldn't conjure up your quiz in time. Please try another category!",
        )
    except HTTPException:
        raise
    except Exception as e:
        details = _exc_details()
        logger.error(
            "Failed to start quiz session",
            quiz_id=str(quiz_id),
            category=request.category,
            error=str(e),
            **details,
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="An unexpected error occurred while starting the quiz. Our wizards have been notified.",
        )
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
    agent_graph: object = Depends(get_agent_graph),
    redis_client: Any = Depends(get_redis_client),
):
    """
    Advance the quiz without submitting an answer.
    This sets ready_for_questions=True and runs the agent to generate baseline questions.
    """
    cache_repo = CacheRepository(redis_client)
    quiz_id_str = str(request.quiz_id)

    current_state = await cache_repo.get_quiz_state(request.quiz_id)
    if not current_state:
        logger.warning("Quiz session not found on proceed", quiz_id=quiz_id_str)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Quiz session not found.",
        )

    # Flip the questions gate and persist snapshot BEFORE scheduling background work
    current_state_dict: GraphState = current_state.model_dump()
    current_state_dict["ready_for_questions"] = True
    await cache_repo.save_quiz_state(current_state_dict)

    structlog.contextvars.bind_contextvars(trace_id=current_state_dict.get("trace_id"))
    logger.info("Proceeding quiz; gate opened for questions", quiz_id=quiz_id_str)

    # Schedule the agent to continue (no answer appended)
    background_tasks.add_task(run_agent_in_background, current_state_dict, redis_client, agent_graph)
    logger.info("Background task scheduled for proceed", quiz_id=quiz_id_str)

    structlog.contextvars.clear_contextvars()
    return ProcessingResponse(status="processing", quiz_id=request.quiz_id)


@router.post(
    "/quiz/next",
    response_model=ProcessingResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit an answer and get next question",
)
async def next_question(
    request: NextQuestionRequest,
    background_tasks: BackgroundTasks,
    agent_graph: object = Depends(get_agent_graph),
    redis_client: Any = Depends(get_redis_client),
    db_session: AsyncSession = Depends(get_db_session),   # NEW
):
    """Append the user's answer to the conversation and continue the agent in the background."""
    cache_repo = CacheRepository(redis_client)
    quiz_id_str = str(request.quiz_id)

    logger.info(
        "Submitting answer for session",
        quiz_id=quiz_id_str,
        answer_present=bool(request.answer),
    )

    current_state = await cache_repo.get_quiz_state(request.quiz_id)
    if not current_state:
        logger.warning("Quiz session not found when submitting answer", quiz_id=quiz_id_str)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Quiz session not found.",
        )

    state_dict: GraphState = current_state.model_dump()

    structlog.contextvars.bind_contextvars(trace_id=state_dict.get("trace_id"))

    logger.debug(
        "Loaded current state from cache (pre-answer)",
        quiz_id=quiz_id_str,
        messages_count=_safe_len(state_dict.get("messages")),
        questions_count=_safe_len(state_dict.get("generated_questions")),
    )

    # Enforce sequential answers *tolerantly*; store typed history; keep transcript message
    try:
        history = list(state_dict.get("quiz_history") or [])
        expected_index = len(history)
        q_index = request.question_index

        # Idempotent duplicate (client re-submitted an already-recorded answer)
        if q_index < expected_index:
            logger.info(
                "Duplicate answer received; treating as success",
                quiz_id=quiz_id_str, q_index=q_index, expected_index=expected_index
            )
            structlog.contextvars.clear_contextvars()
            return ProcessingResponse(status="processing", quiz_id=request.quiz_id)

        # Client skipped ahead (true out-of-order)
        if q_index > expected_index:
            raise HTTPException(status_code=409, detail="Stale or out-of-order answer.")

        server_qs = state_dict.get("generated_questions") or []
        if q_index < 0 or q_index >= len(server_qs):
            raise HTTPException(status_code=400, detail="question_index out of range.")
        q = server_qs[q_index]

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

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to record answer", quiz_id=quiz_id_str, error=str(e), exc_info=True)
        raise HTTPException(status_code=400, detail="Invalid answer payload.")

    # Persist snapshot atomically and continue in background
    updated_state = await cache_repo.update_quiz_state_atomically(request.quiz_id, {
        "quiz_history": new_history,
        "messages": new_messages,
        "ready_for_questions": True,
    })
    if updated_state is None:
        raise HTTPException(status_code=409, detail="Please retry answer submission.")

    updated_state_dict = _to_state_dict(updated_state)

    # NEW: snapshot QA history to DB (best-effort, non-fatal)
    try:
        sess_repo = SessionRepository(db_session)
        await sess_repo.update_qa_history(
            session_id=request.quiz_id,
            qa_history=jsonable_encoder(updated_state_dict.get("quiz_history") or []),  # encode defensively
        )
        await db_session.commit()
    except Exception:
        logger.debug("Non-fatal: failed to snapshot qa_history", quiz_id=quiz_id_str)

    new_answered = len(updated_state_dict.get("quiz_history") or [])
    baseline_count = int(updated_state_dict.get("baseline_count") or 0)
    if new_answered >= baseline_count:
        background_tasks.add_task(run_agent_in_background, updated_state_dict, redis_client, agent_graph)
        logger.info("Background task scheduled for adaptive/finish decision", quiz_id=quiz_id_str)
    else:
        logger.info("Skipping background run (still in baseline phase)", quiz_id=quiz_id_str)

    structlog.contextvars.clear_contextvars()
    return ProcessingResponse(status="processing", quiz_id=request.quiz_id)


@router.get(
    "/quiz/status/{quiz_id}",
    response_model=QuizStatusResponse,
    summary="Poll for quiz status",
)
async def get_quiz_status(
    quiz_id: uuid.UUID,
    redis_client: Any = Depends(get_redis_client),
    known_questions_count: int = Query(
        0,
        ge=0,
        description="The number of questions the client has already received.",
    ),
):
    """
    Returns the next question if available, the final result if finished,
    or 'processing' if the agent is still working.

    Serve the *next unseen* question based on a merge of how many answers the
    server has and how many questions the client reports having seen.
    """
    cache_repo = CacheRepository(redis_client)
    logger.debug(
        "Polling quiz status",
        quiz_id=str(quiz_id),
        known_questions_count=known_questions_count,
    )
    state_model = await cache_repo.get_quiz_state(quiz_id)

    if not state_model:
        logger.warning("Quiz session not found on status poll", quiz_id=str(quiz_id))
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Quiz session not found.",
        )

    state: GraphState = state_model.model_dump()

    structlog.contextvars.bind_contextvars(trace_id=state.get("trace_id"))

    # Final result ready?
    if state.get("final_result"):
        try:
            result = FinalResult.model_validate(state["final_result"])
        except Exception as e:
            logger.error("Malformed final_result in state", quiz_id=str(quiz_id), error=str(e), exc_info=True)
            raise HTTPException(status_code=500, detail="Malformed result data.")
        logger.info("Quiz finished; returning final result", quiz_id=str(quiz_id))
        structlog.contextvars.clear_contextvars()
        return QuizStatusResult(status="finished", type="result", data=result)

    generated: List[Any] = state.get("generated_questions", []) or []
    server_questions_count = len(generated)
    answered_idx = len(state.get("quiz_history") or [])

    target_index = max(answered_idx, known_questions_count)

    logger.debug(
        "Quiz status snapshot",
        quiz_id=str(quiz_id),
        server_questions_count=server_questions_count,
        client_known_questions_count=known_questions_count,
        answered_idx=answered_idx,
        target_index=target_index,
        ready_for_questions=bool(state.get("ready_for_questions")),
    )

    if server_questions_count <= target_index:
        structlog.contextvars.clear_contextvars()
        return ProcessingResponse(status="processing", quiz_id=quiz_id)

    q_raw = generated[target_index]

    try:
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
                img = o.get("image_url")
                if img is None:
                    img = o.get("imageUrl")
                options.append(AnswerOption(text=str(o.get("text", "")), image_url=img))
            else:
                options.append(AnswerOption(text=str(o), image_url=None))

        new_question_api = APIQuestion(text=str(text_val), options=options, image_url=q_image)

    except Exception as e:
        logger.error(
            "Failed to normalize/validate question model",
            quiz_id=str(quiz_id),
            error=str(e),
            **_exc_details(),
            exc_info=True,
        )
        structlog.contextvars.clear_contextvars()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Malformed question data.",
        )

    try:
        state["last_served_index"] = target_index
        await cache_repo.save_quiz_state(state)
    except Exception:
        pass

    logger.info("Returning next unseen question to client", quiz_id=str(quiz_id), index=target_index)
    structlog.contextvars.clear_contextvars()
    return QuizStatusQuestion(status="active", type="question", data=new_question_api)
