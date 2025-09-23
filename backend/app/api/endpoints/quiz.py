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

DB BYPASS:
- Any database usage (session factory injection, passing db_session to graph,
  background-session creation) is commented and left in place for future re-enable.
"""

from __future__ import annotations

import asyncio
import sys
import time
import traceback
import uuid
from typing import Any, Dict, Optional, List

import redis.asyncio as redis
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
from langchain_core.messages import HumanMessage
from pydantic import ValidationError

# -----------------------
# DB-related imports (DB BYPASS)
# -----------------------
# from sqlalchemy.ext.asyncio import AsyncSession
# from app.api.dependencies import async_session_factory, get_db_session

from app.api.dependencies import (
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
from app.services.state_hydration import hydrate_graph_state

# >>> Strongly-typed agent state & schemas (shared with graph)
from app.agent.state import GraphState
from app.agent.schemas import CharacterProfile, Synopsis, QuizQuestion  # noqa: F401 (imported for type clarity)

router = APIRouter()
logger = structlog.get_logger(__name__)


# -----------------------
# Small local utilities
# -----------------------

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
    """
    Normalize an arbitrary agent-produced object into a dict that includes the
    discriminator `type` required by StartQuizPayload.data.

    - If obj is a Pydantic model (v2), use .model_dump()
    - If it's already a dict, copy it
    - Otherwise, validate against our API models and dump

    `variant` must be either "synopsis" or "question".
    """
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
    """
    Normalize agent-produced CharacterProfile-like objects into plain dicts
    so our API CharactersPayload can validate them.
    """
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


# -----------------------
# Graph dependency
# -----------------------

def get_agent_graph(request: Request) -> object:
    """
    Obtains the compiled LangGraph instance created in main.lifespan().
    Returned as `object` to avoid version-specific imports like `CompiledGraph`.
    """
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


# -----------------------
# Background runner
# -----------------------

async def run_agent_in_background(
    state: GraphState,
    redis_client: redis.Redis,
    agent_graph: object,
) -> None:
    """
    Stream the agent in the background and persist the final snapshot to Redis.

    DB BYPASS:
    - Do not create a DB session with async_session_factory()
    - Do not pass db_session in graph config
    """
    # Ensure any cached dicts are coerced back to agent-side models
    state = hydrate_graph_state(state)

    session_id = state.get("session_id")
    session_id_str = str(session_id)
    structlog.contextvars.bind_contextvars(trace_id=state.get("trace_id"))
    cache_repo = CacheRepository(redis_client)

    logger.info(
        "Starting agent graph in background",
        quiz_id=session_id_str,
        state_keys=list(state.keys()),
        messages_count=_safe_len(state.get("messages")),
        generated_questions_count=_safe_len(state.get("generated_questions")),
        generated_characters_count=_safe_len(state.get("generated_characters")),
        ready_for_questions=bool(state.get("ready_for_questions")),
    )
    if _is_local_env():
        try:
            logger.debug(
                "Type check (pre-stream)",
                char_types=[type(c).__name__ for c in (state.get("generated_characters") or [])],
                q_types=[type(q).__name__ for q in (state.get("generated_questions") or [])],
                synopsis_type=type(state.get("category_synopsis")).__name__
                if state.get("category_synopsis") is not None
                else None,
            )
        except Exception:
            pass

    final_state: GraphState = state
    steps = 0
    t_start = time.perf_counter()

    try:
        # --------------------- DB BYPASS ---------------------
        # async with async_session_factory() as db_session:
        #     config = {
        #         "configurable": {
        #             "thread_id": session_id_str,
        #             "db_session": db_session,
        #         }
        #     }
        # ----------------------------------------------------
        config = {"configurable": {"thread_id": session_id_str}}

        logger.debug(
            "Agent background stream starting",
            quiz_id=session_id_str,
            config_keys=list(config.get("configurable", {}).keys()),
        )
        # type: ignore[attr-defined] — duck-typed astream
        async for _ in agent_graph.astream(state, config=config):  # noqa: F821
            steps += 1
            if steps % 5 == 0 and _is_local_env():
                logger.debug(
                    "Agent background progress tick",
                    quiz_id=session_id_str,
                    steps=steps,
                )

        # Fetch the final state snapshot from checkpointer.
        # type: ignore[attr-defined]
        final_state_snapshot = await agent_graph.aget_state(config)  # noqa: F821
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

        # --------------------- DB BYPASS ---------------------
        # Example: persist final session to DB here (future)
        # from app.services.database import SessionRepository
        # async with async_session_factory() as db_session:
        #     repo = SessionRepository(db_session)
        #     await repo.create_from_agent_state(final_state)
        # ----------------------------------------------------

    except Exception as e:
        details = _exc_details()
        logger.error(
            "Agent graph failed in background",
            quiz_id=session_id_str,
            error=str(e),
            **details,
            exc_info=True,
        )
        # Best-effort: annotate transcript
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
        structlog.contextvars.clear_contextvars()


# -----------------------
# Endpoints
# -----------------------

@router.post(
    "/quiz/start",
    response_model=FrontendStartQuizResponse,
    summary="Start a new quiz session",
    status_code=status.HTTP_201_CREATED,
)
async def start_quiz(
    request: StartQuizRequest,
    agent_graph: object = Depends(get_agent_graph),
    redis_client: redis.Redis = Depends(get_redis_client),
    # DB BYPASS: do not inject DB session while testing
    # db_session: AsyncSession = Depends(get_db_session),
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
        "session_id": quiz_id,  # keep as uuid.UUID
        "trace_id": trace_id,
        "category": request.category,
        "messages": [HumanMessage(content=request.category)],
        "error_count": 0,
        "error_message": None,
        "is_error": False,
        "rag_context": None,
        "category_synopsis": None,
        "ideal_archetypes": [],
        "generated_characters": [],
        "generated_questions": [],
        "quiz_history": [],
        "baseline_count": 0,
        "ready_for_questions": False,  # gate closed at start
        "final_result": None,
        # Observability only; does not drive logic
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
        # --------------------- DB BYPASS ---------------------
        # config = {
        #     "configurable": {
        #         "thread_id": str(quiz_id),
        #         "db_session": db_session,
        #     }
        # }
        # ----------------------------------------------------
        config = {"configurable": {"thread_id": str(quiz_id)}}

        logger.debug(
            "Invoking agent graph (initial step)",
            quiz_id=str(quiz_id),
            timeout_seconds=FIRST_STEP_TIMEOUT_S,
            config_keys=list(config.get("configurable", {}).keys()),
        )
        t0 = time.perf_counter()
        # type: ignore[attr-defined]
        state_after_first = await asyncio.wait_for(  # noqa: F821
            agent_graph.ainvoke(initial_state, config),
            timeout=FIRST_STEP_TIMEOUT_S,
        )
        invoke_ms = round((time.perf_counter() - t0) * 1000, 1)
        logger.info(
            "Agent initial step completed",
            quiz_id=str(quiz_id),
            duration_ms=invoke_ms,
            initial_state_present=bool(state_after_first),
        )

        # Accept either key (new 'category_synopsis' or legacy 'synopsis')
        synopsis_obj = state_after_first.get("category_synopsis") or state_after_first.get("synopsis")
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

        # --- Step 2: stream until characters appear or we hit the budget
        have_characters = bool(state_after_first.get("generated_characters"))
        if not have_characters:
            t_stream_start = time.perf_counter()
            steps = 0
            # type: ignore[attr-defined]
            async for _ in agent_graph.astream(state_after_first, config=config):  # noqa: F821
                steps += 1
                current = await agent_graph.aget_state(config)  # noqa: F821
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
            # Re-pull synopsis in case the streaming step swapped state object
            payload_synopsis = state_after_first.get("category_synopsis") or state_after_first.get("synopsis")
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
        # Return Pydantic model instance to ensure camelCase (by_alias) on the wire
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
    redis_client: redis.Redis = Depends(get_redis_client),
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

    # Normalize cached dicts → agent-side Pydantic models
    current_state = hydrate_graph_state(current_state)

    # Flip the questions gate and persist snapshot BEFORE scheduling background work
    current_state["ready_for_questions"] = True
    await cache_repo.save_quiz_state(current_state)

    structlog.contextvars.bind_contextvars(trace_id=current_state.get("trace_id"))
    logger.info("Proceeding quiz; gate opened for questions", quiz_id=quiz_id_str)

    # Schedule the agent to continue (no answer appended)
    background_tasks.add_task(run_agent_in_background, current_state, redis_client, agent_graph)
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
    redis_client: redis.Redis = Depends(get_redis_client),
):
    """
    Append the user's answer to the conversation and continue the agent in the background.
    """
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

    # Normalize cached dicts → agent-side Pydantic models
    current_state = hydrate_graph_state(current_state)

    structlog.contextvars.bind_contextvars(trace_id=current_state.get("trace_id"))

    logger.debug(
        "Loaded current state from cache (pre-answer)",
        quiz_id=quiz_id_str,
        messages_count=_safe_len(current_state.get("messages")),
        questions_count=_safe_len(current_state.get("generated_questions")),
    )

    # Enforce sequential answers *tolerantly*; store typed history; keep transcript message
    try:
        # Latest server history is the source of truth
        history = list(current_state.get("quiz_history") or [])
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

        server_qs = current_state.get("generated_questions") or []
        if q_index < 0 or q_index >= len(server_qs):
            raise HTTPException(status_code=400, detail="question_index out of range.")
        q = server_qs[q_index]

        # Normalize question dict
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

        # Prepare atomic update payload
        new_history = [*history, {
            "question_index": q_index,  # record which question this answer belongs to
            "question_text": qd.get("question_text", ""),
            "answer_text": ans_text,
            "option_index": option_index,
        }]
        new_messages = list(current_state.get("messages") or [])
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
        # Session expired or write contention; surface a retriable error
        raise HTTPException(status_code=409, detail="Please retry answer submission.")

    background_tasks.add_task(run_agent_in_background, updated_state, redis_client, agent_graph)
    logger.info("Background task scheduled for next step", quiz_id=quiz_id_str)

    structlog.contextvars.clear_contextvars()
    return ProcessingResponse(status="processing", quiz_id=request.quiz_id)


@router.get(
    "/quiz/status/{quiz_id}",
    response_model=QuizStatusResponse,
    summary="Poll for quiz status",
)
async def get_quiz_status(
    quiz_id: uuid.UUID,
    redis_client: redis.Redis = Depends(get_redis_client),
    known_questions_count: int = Query(
        0,
        ge=0,
        description="The number of questions the client has already received.",
    ),
):
    """
    Returns the next question if available, the final result if finished,
    or 'processing' if the agent is still working.

    FIX: Serve the *next unseen* question based on a server-safe merge of:
         - how many the client says they've already seen (known_questions_count)
         - how many answers we've actually recorded (len(quiz_history))

         target_index = max(len(quiz_history), known_questions_count)

    This prevents re-serving Q0 when the client has already advanced to Q1,
    which previously caused 409 on /quiz/next due to index drift.
    """
    cache_repo = CacheRepository(redis_client)
    logger.debug(
        "Polling quiz status",
        quiz_id=str(quiz_id),
        known_questions_count=known_questions_count,
    )
    state = await cache_repo.get_quiz_state(quiz_id)

    if not state:
        logger.warning("Quiz session not found on status poll", quiz_id=str(quiz_id))
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Quiz session not found.",
        )

    # Normalize cached dicts → agent-side Pydantic models
    state = hydrate_graph_state(state)

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

    # Any new questions beyond what is (answers or client-known)?
    generated: List[Any] = state.get("generated_questions", []) or []
    server_questions_count = len(generated)
    answered_idx = len(state.get("quiz_history") or [])

    # Decide the *next unseen* index to serve without going backwards
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
        logger.info("No new questions to serve at target_index; still processing", quiz_id=str(quiz_id))
        structlog.contextvars.clear_contextvars()
        return ProcessingResponse(status="processing", quiz_id=quiz_id)

    # Serve the question at target_index
    q_raw = generated[target_index]

    try:
        if hasattr(q_raw, "model_dump"):
            qd = q_raw.model_dump()
        elif isinstance(q_raw, dict):
            qd = dict(q_raw)
        else:
            # Try to coerce via our internal model first
            qd = QuizQuestion.model_validate(q_raw).model_dump()

        text = qd.get("question_text", "") or qd.get("text", "")
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

        new_question_api = APIQuestion(text=str(text), options=options, image_url=q_image)

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

    # Observability: record what we served (non-authoritative)
    try:
        state["last_served_index"] = target_index
        await cache_repo.save_quiz_state(state)
    except Exception:
        # best-effort only
        pass

    logger.info("Returning next unseen question to client", quiz_id=str(quiz_id), index=target_index)
    structlog.contextvars.clear_contextvars()
    return QuizStatusQuestion(status="active", type="question", data=new_question_api)
