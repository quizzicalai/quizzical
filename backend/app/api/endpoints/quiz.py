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
from typing import Annotated, Any, Dict, List, Optional, Tuple

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
    get_redis_client,
    verify_turnstile,
)
from app.core.config import settings
from app.models.api import (
    AnswerOption,
    CharactersPayload,
    FinalResult,
    FrontendStartQuizResponse,
    NextQuestionRequest,
    ProceedRequest,
    ProcessingResponse,
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

@router.post(
    "/quiz/start",
    response_model=FrontendStartQuizResponse,
    summary="Start a new quiz session",
    status_code=status.HTTP_201_CREATED,
)
async def start_quiz(
    request: StartQuizRequest,
    agent_graph: Annotated[object, Depends(get_agent_graph)],
    redis_client: Annotated[Any, Depends(get_redis_client)],
    db_session: Annotated[AsyncSession, Depends(get_db_session)],
    turnstile_verified: Annotated[bool, Depends(verify_turnstile)],
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

    # Initial graph state
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

        # Save & Persist Synopsis
        await cache_repo.save_quiz_state(state_after_first)

        try:
            # Build agent_plan
            ideal_archetypes = state_after_first.get("ideal_archetypes") or []
            plan_from_state = state_after_first.get("agent_plan") or {}
            if not plan_from_state:
                syn_dict = _serialize_synopsis(state_after_first.get("synopsis"))
                plan_from_state = {
                    "title": syn_dict.get("title", ""),
                    "synopsis": syn_dict.get("summary", ""),
                    "ideal_archetypes": list(ideal_archetypes),
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
        except Exception:
            logger.exception("Failed to persist initial session snapshot", quiz_id=str(quiz_id))

        # --- Step 2: Stream characters if needed ---
        if not state_after_first.get("generated_characters"):
            state_after_first = await _stream_characters_until_budget(
                agent_graph, config, state_after_first, quiz_id, request.category,
                db_session, STREAM_BUDGET_S
            )

        # --- Step 3: Build Response ---
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


# ---------------------------------------------------------------------------
# Next Question Helpers (Extracted to fix C901)
# ---------------------------------------------------------------------------

def _validate_and_record_answer(state_dict: Dict, request: NextQuestionRequest) -> Tuple[List[Dict], List[Any]]:
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

    current_state = await cache_repo.get_quiz_state(request.quiz_id)
    if not current_state:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quiz session not found.")

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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quiz session not found.")

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
    generated: List[Any] = state.get("generated_questions", []) or []
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

    # Update last served pointer
    try:
        state["last_served_index"] = target_index
        await cache_repo.save_quiz_state(state)
    except Exception:
        pass

    logger.info("Returning next unseen question to client", quiz_id=str(quiz_id), index=target_index)
    structlog.contextvars.clear_contextvars()
    return QuizStatusQuestion(status="active", type="question", data=new_question_api)
