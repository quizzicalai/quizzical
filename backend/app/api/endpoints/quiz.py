"""
API Endpoints for Quiz Interaction

This module contains the FastAPI routes for starting a quiz, submitting answers,
and polling for the status of the asynchronous agent.
"""
import asyncio
import uuid
from typing import Annotated

import redis.asyncio as redis
import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, status
from langchain_core.messages import HumanMessage
from langgraph.prebuilt import Pregel
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

router = APIRouter()
logger = structlog.get_logger(__name__)

def get_agent_graph(request: Request) -> Pregel:
    """Dependency to retrieve the agent graph from the application state."""
    agent_graph = getattr(request.app.state, "agent_graph", None)
    if agent_graph is None:
        logger.error("Agent graph not found in application state. The app may not have started correctly.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Agent service is not available.",
        )
    return agent_graph

async def run_agent_in_background(
    state: GraphState,
    redis_client: redis.Redis,
    agent_graph: Pregel,
):
    """
    A wrapper to run the agent graph asynchronously and ensure the final
    state is always saved back to the cache.
    """
    session_id = state.get("session_id")
    structlog.contextvars.bind_contextvars(trace_id=state.get("trace_id"))
    cache_repo = CacheRepository(redis_client)
    session_id_str = str(session_id)
    logger.info("Starting agent graph in background...", quiz_id=session_id_str)

    final_state = state
    try:
        # Create a new session within the background task for the agent tools
        async with async_session_factory() as db_session:
            config = {
                "configurable": {
                    "thread_id": session_id_str,
                    "db_session": db_session,
                }
            }
            # The agent graph is invoked as a stream to process all steps.
            async for _ in agent_graph.astream(state, config=config):
                pass  # Consume the stream to run the graph

            # After the stream is consumed, get the final state
            final_state_result = await agent_graph.aget_state(config)
            final_state = final_state_result.values
        
        logger.info("Agent graph finished in background.", quiz_id=session_id_str)

    except Exception as e:
        logger.error("Agent graph failed in background.", quiz_id=session_id_str, error=str(e), exc_info=True)
        error_message = HumanMessage(content=f"Agent failed with error: {e}")
        if "messages" in final_state:
            final_state["messages"].append(error_message)
    finally:
        await cache_repo.save_quiz_state(final_state)
        logger.info("Final agent state saved to cache.", quiz_id=session_id_str)
    structlog.contextvars.clear_contextvars()


@router.post(
    "/quiz/start",
    response_model=FrontendStartQuizResponse,
    summary="Start a new quiz session",
    status_code=status.HTTP_201_CREATED,
)
async def start_quiz(
    request: StartQuizRequest,
    agent_graph: Annotated[Pregel, Depends(get_agent_graph)],
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

    logger.info("Starting new quiz session", quiz_id=str(quiz_id), category=request.category)

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

    try:
        # Run just the first step of the agent synchronously to get the plan
        config = {
            "configurable": {
                "thread_id": str(quiz_id),
                "db_session": db_session,
            }
        }
        initial_step_state = await asyncio.wait_for(agent_graph.ainvoke(initial_state, config), timeout=60.0)

        synopsis_obj = initial_step_state.get("category_synopsis")
        if not initial_step_state or not synopsis_obj:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="The AI agent failed to generate a quiz plan. Please try a different category.",
            )

        # Save the state with the initial plan to Redis
        await cache_repo.save_quiz_state(initial_step_state)

        synopsis_payload = StartQuizPayload(
            type="synopsis",
            data=synopsis_obj
        )

        return FrontendStartQuizResponse(
            quiz_id=quiz_id,
            initial_payload=synopsis_payload
        )

    except asyncio.TimeoutError:
        logger.warning("Quiz start process timed out after 60 seconds.", quiz_id=str(quiz_id))
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Our crystal ball is a bit cloudy and we couldn't conjure up your quiz in time. Please try another category!",
        )
    except Exception as e:
        logger.error("Failed to start quiz session", quiz_id=str(quiz_id), error=str(e), exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="An unexpected error occurred while starting the quiz. Our wizards have been notified.",
        )


@router.post(
    "/quiz/next",
    response_model=ProcessingResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit an answer and get next question",
)
async def next_question(
    request: NextQuestionRequest,
    background_tasks: BackgroundTasks,
    agent_graph: Annotated[Pregel, Depends(get_agent_graph)],
    redis_client: Annotated[redis.Redis, Depends(get_redis_client)],
):
    """
    Submits a user's answer and triggers the asynchronous background processing
    for the next question or the final result.
    """
    cache_repo = CacheRepository(redis_client)
    quiz_id_str = str(request.quiz_id)
    logger.info("Submitting answer for session", quiz_id=quiz_id_str)

    current_state = await cache_repo.get_quiz_state(request.quiz_id)
    if not current_state:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quiz session not found.")

    current_state["messages"].append(HumanMessage(content=f"My answer is: {request.answer}"))

    background_tasks.add_task(run_agent_in_background, current_state, redis_client, agent_graph)

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
    state = await cache_repo.get_quiz_state(quiz_id)

    if not state:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quiz session not found.")

    if state.get("final_result"):
        return {"status": "finished", "type": "result", "data": state["final_result"]}

    server_questions_count = len(state.get("generated_questions", []))

    if server_questions_count > known_questions_count:
        new_question_internal = state["generated_questions"][-1]
        new_question_api = APIQuestion.model_validate(new_question_internal)
        return {"status": "active", "type": "question", "data": new_question_api}

    return {"status": "processing", "quiz_id": quiz_id}