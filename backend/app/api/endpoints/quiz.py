# app/api/endpoints/quiz.py
"""
API Endpoints for Quiz Interaction

This module contains the FastAPI routes for starting a quiz, submitting answers,
and polling for the status of the asynchronous agent.

Key improvements in this version:
- /quiz/start blocks (within a strict time budget) until a synopsis is available
  and tries to stream initial characters within a separate time budget.
- Returns characters via CharactersPayload (schema-level improvement).
- /quiz/status now returns the next *unseen* question based on known_questions_count.
- Adds /quiz/proceed to advance from synopsis/characters to question generation
  without requiring a dummy 'answer'.
- Time budgets are read from settings with safe fallbacks (30s).
"""
from __future__ import annotations

import asyncio
import sys
import time
import traceback
import uuid
from typing import Optional

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
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.state import GraphState
from app.api.dependencies import (
    async_session_factory,
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
    QuizStatusResponse,
    StartQuizPayload,
    StartQuizRequest,
    ProceedRequest,
)
from app.services.redis_cache import CacheRepository

router = APIRouter()
logger = structlog.get_logger(__name__)


# -----------------------
# Small local utilities
# -----------------------

def _is_local_env() -> bool:
    try:
        return (settings.APP_ENVIRONMENT or "local").lower() in {
            "local",
            "dev",
            "development",
        }
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
    Stream the remaining agent steps in the background. Always attempts to save
    the final state to Redis—even on failure.
    """
    # We only rely on duck-typing: agent_graph has astream/aget_state
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
    )

    final_state = state
    steps = 0
    t_start = time.perf_counter()

    try:
        # Each background run gets its own DB session (tools consume via RunnableConfig)
        async with async_session_factory() as db_session:
            config = {
                "configurable": {
                    "thread_id": session_id_str,
                    "db_session": db_session,
                }
            }
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
            # astream fully consumed. Fetch the final state snapshot from checkpointer.
            # type: ignore[attr-defined] — duck-typed aget_state
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

    except Exception as e:
        details = _exc_details()
        logger.error(
            "Agent graph failed in background",
            quiz_id=session_id_str,
            error=str(e),
            **details,
            exc_info=True,
        )
        # best-effort annotate messages so the transcript shows a failure
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
    db_session: AsyncSession = Depends(get_db_session),
    turnstile_verified: bool = Depends(verify_turnstile),
):
    """
    Starts a quiz session and (within a strict time budget) waits for:
      1) Generated synopsis
      2) Attempts to stream initial character set within a separate budget

    We save state snapshots to Redis so the client can poll while the agent runs.
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
        env=settings.APP_ENVIRONMENT,
    )

    if _is_local_env():
        try:
            tool_models = {k: v.model_name for k, v in (settings.llm_tools or {}).items()}
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
        "rag_context": None,
        "category_synopsis": None,
        "ideal_archetypes": [],
        "generated_characters": [],
        "generated_questions": [],
        "final_result": None,
    }

    logger.debug(
        "Prepared initial graph state",
        state_keys=list(initial_state.keys()),
        messages_count=_safe_len(initial_state.get("messages")),
        questions_count=_safe_len(initial_state.get("generated_questions")),
        characters_count=_safe_len(initial_state.get("generated_characters")),
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
        config = {
            "configurable": {
                "thread_id": str(quiz_id),
                "db_session": db_session,
            }
        }
        logger.debug(
            "Invoking agent graph (initial step)",
            quiz_id=str(quiz_id),
            timeout_seconds=FIRST_STEP_TIMEOUT_S,
            config_keys=list(config.get("configurable", {}).keys()),
        )
        t0 = time.perf_counter()
        # type: ignore[attr-defined] — duck-typed ainvoke
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

        synopsis_obj = state_after_first.get("category_synopsis")
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
            # type: ignore[attr-defined] — duck-typed astream/aget_state
            async for _ in agent_graph.astream(state_after_first, config=config):  # noqa: F821
                steps += 1
                current = await agent_graph.aget_state(config)  # noqa: F821
                current_values = current.values
                have_characters = bool(current_values.get("generated_characters"))
                if have_characters:
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

        # Build response payload(s)
        synopsis_payload = StartQuizPayload(type="synopsis", data=state_after_first["category_synopsis"])
        characters = state_after_first.get("generated_characters", []) or []

        characters_payload = CharactersPayload(data=characters) if characters else None

        logger.info(
            "Quiz session ready for client",
            quiz_id=str(quiz_id),
            has_characters=bool(characters),
            character_count=len(characters),
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
    redis_client: redis.Redis = Depends(get_redis_client),
):
    """
    Advance the quiz without submitting an answer. This lets the agent begin
    baseline question generation after the user reviews synopsis/characters.
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

    structlog.contextvars.bind_contextvars(trace_id=current_state.get("trace_id"))
    logger.info("Proceeding quiz without answer", quiz_id=quiz_id_str)

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

    structlog.contextvars.bind_contextvars(trace_id=current_state.get("trace_id"))

    logger.debug(
        "Loaded current state from cache (pre-answer)",
        quiz_id=quiz_id_str,
        messages_count=_safe_len(current_state.get("messages")),
        questions_count=_safe_len(current_state.get("generated_questions")),
    )

    # Append the answer as a human message; agent will generate next question/result
    try:
        answer_text = "" if request.answer is None else str(request.answer)
        current_state["messages"].append(HumanMessage(content=f"My answer is: {answer_text}"))
    except Exception as e:
        logger.error("Failed to append answer to state messages", quiz_id=quiz_id_str, error=str(e), exc_info=True)
        raise HTTPException(status_code=400, detail="Invalid answer payload.")

    background_tasks.add_task(run_agent_in_background, current_state, redis_client, agent_graph)
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

    Improvement: serve the *next unseen* question (index == known_questions_count),
    not the last one generated.
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

    structlog.contextvars.bind_contextvars(trace_id=state.get("trace_id"))

    # Final result ready?
    if state.get("final_result"):
        logger.info("Quiz finished; returning final result", quiz_id=str(quiz_id))
        structlog.contextvars.clear_contextvars()
        return {"status": "finished", "type": "result", "data": state["final_result"]}

    # Any new questions beyond what the client already knows?
    generated = state.get("generated_questions", []) or []
    server_questions_count = len(generated)
    logger.debug(
        "Quiz status snapshot",
        quiz_id=str(quiz_id),
        server_questions_count=server_questions_count,
        client_known_questions_count=known_questions_count,
    )

    if server_questions_count > known_questions_count:
        # Serve the next unseen question (preserves order)
        next_index = known_questions_count
        try:
            new_question_api = APIQuestion.model_validate(generated[next_index])
        except Exception as e:
            logger.error(
                "Failed to validate question model",
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
        logger.info("Returning next unseen question to client", quiz_id=str(quiz_id), index=next_index)
        structlog.contextvars.clear_contextvars()
        return {"status": "active", "type": "question", "data": new_question_api}

    logger.info("No new questions; still processing", quiz_id=str(quiz_id))
    structlog.contextvars.clear_contextvars()
    return {"status": "processing", "quiz_id": quiz_id}
