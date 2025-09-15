"""
API Endpoints for Quiz Interaction

This module contains the FastAPI routes for starting a quiz, submitting answers,
and polling for the status of the asynchronous agent.
"""
import asyncio
import uuid
import sys
import time
import traceback
from typing import Annotated, TYPE_CHECKING, Optional

import redis.asyncio as redis
import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from langchain_core.messages import HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.state import GraphState
from app.api.dependencies import async_session_factory, get_db_session, get_redis_client, verify_turnstile
from app.models.api import (
    FrontendStartQuizResponse,
    NextQuestionRequest,
    ProcessingResponse,
    Question as APIQuestion,
    QuizStatusResponse,
    StartQuizPayload,
    StartQuizRequest,
)
from app.services.redis_cache import CacheRepository
from app.core.config import settings  # added: to gate local-only debug logs

# Use TYPE_CHECKING to avoid circular imports and runtime issues
if TYPE_CHECKING:
    from langgraph.graph import CompiledGraph

router = APIRouter()
logger = structlog.get_logger(__name__)


def _is_local_env() -> bool:
    """Helper to determine if we're running in local/dev environment."""
    try:
        return (settings.APP_ENVIRONMENT or "local").lower() in {"local", "dev", "development"}
    except Exception:
        # Be conservative; if settings is misconfigured, avoid crashing logging
        return False


def _safe_len(obj) -> Optional[int]:
    try:
        return len(obj)  # type: ignore[arg-type]
    except Exception:
        return None


def _exc_details() -> dict:
    exc_type, exc_value, exc_traceback = sys.exc_info()
    return {
        "error_type": exc_type.__name__ if exc_type else "Unknown",
        "error_message": str(exc_value) if exc_value else "",
        "traceback": traceback.format_exc() if exc_traceback else "",
    }


def get_agent_graph(request: Request) -> "CompiledGraph":
    """Dependency to retrieve the agent graph from the application state."""
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
    # Added: lightweight debug about the graph presence
    logger.debug(
        "Agent graph loaded from app state",
        has_agent_graph=True,
        agent_graph_type=type(agent_graph).__name__,
        agent_graph_id=id(agent_graph),
    )
    return agent_graph


async def run_agent_in_background(
    state: GraphState,
    redis_client: redis.Redis,
    agent_graph: "CompiledGraph",
):
    """
    A wrapper to run the agent graph asynchronously and ensure the final
    state is always saved back to the cache.
    """
    session_id = state.get("session_id")
    structlog.contextvars.bind_contextvars(trace_id=state.get("trace_id"))
    cache_repo = CacheRepository(redis_client)
    session_id_str = str(session_id)

    # Added: initial diagnostics about incoming state
    logger.info(
        "Starting agent graph in background...",
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
        # Create a new session within the background task for the agent tools
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
            # The agent graph is invoked as a stream to process all steps.
            async for _ in agent_graph.astream(state, config=config):
                steps += 1  # Added: progress counter for visibility
                if steps % 5 == 0 and _is_local_env():
                    logger.debug("Agent background progress tick", quiz_id=session_id_str, steps=steps)
                # pass  # original behavior (consuming the stream)
            # After the stream is consumed, get the final state
            final_state_result = await agent_graph.aget_state(config)
            final_state = final_state_result.values

        duration_ms = round((time.perf_counter() - t_start) * 1000, 1)
        logger.info(
            "Agent graph finished in background.",
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
            "Agent graph failed in background.",
            quiz_id=session_id_str,
            error=str(e),
            **details,
            exc_info=True,
        )
        error_message = HumanMessage(content=f"Agent failed with error: {e}")
        if isinstance(final_state, dict) and "messages" in final_state:
            try:
                final_state["messages"].append(error_message)
            except Exception:
                logger.debug("Could not append error message to final_state.messages", quiz_id=session_id_str)
    finally:
        try:
            t_save = time.perf_counter()
            await cache_repo.save_quiz_state(final_state)
            save_ms = round((time.perf_counter() - t_save) * 1000, 1)
            logger.info("Final agent state saved to cache.", quiz_id=session_id_str, save_duration_ms=save_ms)
        except Exception as e:
            logger.error(
                "Failed to save final agent state to cache.",
                quiz_id=session_id_str,
                error=str(e),
                **_exc_details(),
                exc_info=True,
            )
        structlog.contextvars.clear_contextvars()


@router.post(
    "/quiz/start",
    response_model=FrontendStartQuizResponse,
    summary="Start a new quiz session",
    status_code=status.HTTP_201_CREATED,
)
async def start_quiz(
    request: StartQuizRequest,
    agent_graph: Annotated["CompiledGraph", Depends(get_agent_graph)],
    redis_client: Annotated[redis.Redis, Depends(get_redis_client)],
    db_session: Annotated[AsyncSession, Depends(get_db_session)],
    turnstile_verified: Annotated[bool, Depends(verify_turnstile)],
):
    """
    Initiates a new quiz session. This is a synchronous, blocking endpoint
    that performs the initial AI planning to generate the quiz synopsis.
    """
    quiz_id = uuid.uuid4()
    trace_id = str(uuid.uuid4())
    cache_repo = CacheRepository(redis_client)

    # Added: bind trace_id for consistent correlation
    structlog.contextvars.bind_contextvars(trace_id=trace_id)

    # Added: initial diagnostics (keep secrets out)
    logger.info(
        "Starting new quiz session",
        quiz_id=str(quiz_id),
        category=request.category,
        turnstile_verified=bool(turnstile_verified),
        env=settings.APP_ENVIRONMENT,
    )
    if _is_local_env():
        # Lightweight snapshot of relevant config state for local debugging
        try:
            tool_models = {k: v.model_name for k, v in (settings.llm_tools or {}).items()}
        except Exception:
            tool_models = {}
        logger.debug(
            "LLM configuration snapshot (local only)",
            default_llm_model=getattr(settings, "default_llm_model", None),
            llm_tool_models=tool_models,
            prompt_keys=list((settings.llm_prompts or {}).keys()),
        )

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

    # Added: log constructed initial state shape (no PII; sizes only)
    logger.debug(
        "Prepared initial graph state",
        state_keys=list(initial_state.keys()),
        messages_count=_safe_len(initial_state.get("messages")),
        questions_count=_safe_len(initial_state.get("generated_questions")),
        characters_count=_safe_len(initial_state.get("generated_characters")),
    )

    try:
        # Run just the first step of the agent synchronously to get the plan
        config = {
            "configurable": {
                "thread_id": str(quiz_id),
                "db_session": db_session,
            }
        }
        logger.debug(
            "Invoking agent graph (initial step)",
            quiz_id=str(quiz_id),
            timeout_seconds=60.0,
            config_keys=list(config.get("configurable", {}).keys()),
        )
        t0 = time.perf_counter()
        initial_step_state = await asyncio.wait_for(agent_graph.ainvoke(initial_state, config), timeout=60.0)
        invoke_ms = round((time.perf_counter() - t0) * 1000, 1)
        logger.info(
            "Agent initial step completed",
            quiz_id=str(quiz_id),
            duration_ms=invoke_ms,
            initial_state_present=bool(initial_step_state),
        )
        logger.debug(
            "Agent initial step result snapshot",
            result_keys=list(initial_step_state.keys()) if initial_step_state else None,
            has_synopsis=bool(initial_step_state.get("category_synopsis")) if initial_step_state else False,
            messages_count=_safe_len(initial_step_state.get("messages")) if initial_step_state else None,
            questions_count=_safe_len(initial_step_state.get("generated_questions")) if initial_step_state else None,
        )

        synopsis_obj = initial_step_state.get("category_synopsis")
        if not initial_step_state or not synopsis_obj:
            logger.error(
                "Agent failed to generate synopsis",
                quiz_id=str(quiz_id),
                initial_step_state_present=bool(initial_step_state),
                has_synopsis=bool(synopsis_obj),
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="The AI agent failed to generate a quiz plan. Please try a different category.",
            )

        # Save the state with the initial plan to Redis
        t_save = time.perf_counter()
        await cache_repo.save_quiz_state(initial_step_state)
        save_ms = round((time.perf_counter() - t_save) * 1000, 1)
        logger.info(
            "Saved initial quiz state to cache",
            quiz_id=str(quiz_id),
            save_duration_ms=save_ms,
        )

        synopsis_payload = StartQuizPayload(
            type="synopsis",
            data=synopsis_obj
        )

        logger.info(
            "Quiz session started successfully",
            quiz_id=str(quiz_id),
            synopsis_title=getattr(synopsis_obj, "title", None),
        )

        return FrontendStartQuizResponse(
            quiz_id=quiz_id,
            initial_payload=synopsis_payload
        )

    except asyncio.TimeoutError:
        logger.warning(
            "Quiz start process timed out after 60 seconds.",
            quiz_id=str(quiz_id),
            category=request.category,
        )
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Our crystal ball is a bit cloudy and we couldn't conjure up your quiz in time. Please try another category!",
        )
    except Exception as e:
        # Added: richer unexpected error diagnostics (log only; response unchanged)
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
    "/quiz/next",
    response_model=ProcessingResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit an answer and get next question",
)
async def next_question(
    request: NextQuestionRequest,
    background_tasks: BackgroundTasks,
    agent_graph: Annotated["CompiledGraph", Depends(get_agent_graph)],
    redis_client: Annotated[redis.Redis, Depends(get_redis_client)],
):
    """
    Submits a user's answer and triggers the asynchronous background processing
    for the next question or the final result.
    """
    cache_repo = CacheRepository(redis_client)
    quiz_id_str = str(request.quiz_id)

    # Added: correlate logs with stored state trace_id if possible
    logger.info(
        "Submitting answer for session",
        quiz_id=quiz_id_str,
        answer_present=bool(request.answer),
    )

    current_state = await cache_repo.get_quiz_state(request.quiz_id)
    if not current_state:
        logger.warning("Quiz session not found when submitting answer", quiz_id=quiz_id_str)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quiz session not found.")

    structlog.contextvars.bind_contextvars(trace_id=current_state.get("trace_id"))

    # Added: snapshot of state before mutating
    logger.debug(
        "Loaded current state from cache (pre-answer)",
        quiz_id=quiz_id_str,
        messages_count=_safe_len(current_state.get("messages")),
        questions_count=_safe_len(current_state.get("generated_questions")),
    )

    current_state["messages"].append(HumanMessage(content=f"My answer is: {request.answer}"))

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
    redis_client: Annotated[redis.Redis, Depends(get_redis_client)],
    known_questions_count: int = Query(0, ge=0, description="The number of questions the client has already received."),
):
    """
    Polls for the result of the asynchronous processing. The client can pass
    `known_questions_count` to prevent receiving the same question multiple times.
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quiz session not found.")

    structlog.contextvars.bind_contextvars(trace_id=state.get("trace_id"))

    if state.get("final_result"):
        logger.info("Quiz finished; returning final result", quiz_id=str(quiz_id))
        structlog.contextvars.clear_contextvars()
        return {"status": "finished", "type": "result", "data": state["final_result"]}

    server_questions_count = len(state.get("generated_questions", []))
    logger.debug(
        "Quiz status snapshot",
        quiz_id=str(quiz_id),
        server_questions_count=server_questions_count,
        client_known_questions_count=known_questions_count,
    )

    if server_questions_count > known_questions_count:
        new_question_internal = state["generated_questions"][-1]
        try:
            new_question_api = APIQuestion.model_validate(new_question_internal)
        except Exception as e:
            # Added: visibility if model validation fails
            logger.error(
                "Failed to validate question model",
                quiz_id=str(quiz_id),
                error=str(e),
                **_exc_details(),
                exc_info=True,
            )
            structlog.contextvars.clear_contextvars()
            raise
        logger.info("Returning new question to client", quiz_id=str(quiz_id))
        structlog.contextvars.clear_contextvars()
        return {"status": "active", "type": "question", "data": new_question_api}

    logger.info("No new questions; still processing", quiz_id=str(quiz_id))
    structlog.contextvars.clear_contextvars()
    return {"status": "processing", "quiz_id": quiz_id}
