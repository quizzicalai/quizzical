# backend/app/api/endpoints/quiz.py
"""
API Endpoints for Quiz Interaction

This module contains the FastAPI routes for starting a quiz, submitting answers,
and polling for the status of the asynchronous agent.
"""
import asyncio
import uuid
from typing import Optional

import redis.asyncio as redis
import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from langchain_core.messages import HumanMessage

from app.agent.graph import agent_graph
from app.agent.state import GraphState, QuizQuestion
from app.api.dependencies import get_redis_client, verify_turnstile
from app.models.api import (
    NextQuestionRequest,
    ProcessingResponse,
    Question as APIQuestion,
    QuizStatusResponse,
    StartQuizRequest,
    StartQuizResponse,
)
from app.services.redis_cache import CacheRepository

router = APIRouter()
logger = structlog.get_logger(__name__)


async def run_agent_in_background(state: GraphState, redis_client: redis.Redis):
    """
    A wrapper to run the agent graph asynchronously and ensure the final
    state is always saved back to the cache.
    """
    structlog.contextvars.bind_contextvars(trace_id=state["trace_id"])
    cache_repo = CacheRepository(redis_client)
    session_id_str = str(state["session_id"])
    logger.info("Starting agent graph in background...", session_id=session_id_str)
    
    final_state = state
    try:
        final_state = await agent_graph.ainvoke(state)
        logger.info("Agent graph finished in background.", session_id=session_id_str)
    except Exception as e:
        logger.error("Agent graph failed in background.", session_id=session_id_str, error=str(e))
        error_message = HumanMessage(content=f"Agent failed with error: {e}")
        final_state["messages"].append(error_message)
    finally:
        await cache_repo.save_quiz_state(final_state)
        logger.info("Final agent state saved to cache.", session_id=session_id_str)
    structlog.contextvars.clear_contextvars()


@router.post(
    "/quiz/start",
    response_model=StartQuizResponse,
    summary="Start a new quiz session",
    status_code=status.HTTP_201_CREATED,
)
async def start_quiz(
    request: StartQuizRequest,
    redis_client: redis.Redis = Depends(get_redis_client),
    # The verify_turnstile dependency is added here to protect the endpoint.
    # It runs before the main function logic.
    turnstile_verified: bool = Depends(verify_turnstile),
):
    """
    Initiates a new quiz session. This is a synchronous, blocking endpoint
    that performs the initial AI planning and content generation required
    to create the first question, with a hard timeout of 60 seconds.
    """
    session_id = uuid.uuid4()
    trace_id = str(uuid.uuid4())
    cache_repo = CacheRepository(redis_client)

    logger.info("Starting new quiz session", session_id=str(session_id), category=request.category)

    initial_state: GraphState = {
        "session_id": session_id,
        "trace_id": trace_id,
        "category": request.category,
        "messages": [HumanMessage(content=f"Create a quiz about {request.category}")],
        "error_count": 0,
        "rag_context": None,
        "category_synopsis": None,
        "generated_characters": [],
        "generated_questions": [],
        "final_result": None,
    }

    try:
        final_state = await asyncio.wait_for(agent_graph.ainvoke(initial_state), timeout=60.0)
        
        if not final_state or not final_state.get("generated_questions"):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="The AI agent failed to generate the first question. Please try a different category.",
            )

        await cache_repo.save_quiz_state(final_state)
        
        first_question_internal: QuizQuestion = final_state["generated_questions"][0]
        first_question_api = APIQuestion.model_validate(first_question_internal)

        return StartQuizResponse(quiz_id=session_id, question=first_question_api)
    
    except asyncio.TimeoutError:
        logger.warning("Quiz start process timed out after 60 seconds.", session_id=str(session_id))
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Our crystal ball is a bit cloudy and we couldn't conjure up your quiz in time. Please try another category!",
        )
    except Exception as e:
        logger.error("Failed to start quiz session", session_id=str(session_id), error=str(e))
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
    redis_client: redis.Redis = Depends(get_redis_client),
):
    """
    Submits a user's answer and triggers the asynchronous background processing
    for the next question or the final result.
    """
    cache_repo = CacheRepository(redis_client)
    session_id_str = str(request.quiz_id)
    logger.info("Submitting answer for session", session_id=session_id_str)

    current_state = await cache_repo.get_quiz_state(request.quiz_id)
    if not current_state:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quiz session not found.")

    current_state["messages"].append(HumanMessage(content=f"My answer is: {request.answer}"))

    background_tasks.add_task(run_agent_in_background, current_state, redis_client)

    return ProcessingResponse(status="processing", quiz_id=request.quiz_id)


@router.get(
    "/quiz/status/{quiz_id}",
    response_model=QuizStatusResponse,
    summary="Poll for quiz status",
)
async def get_quiz_status(
    quiz_id: uuid.UUID,
    known_questions_count: int = Query(0, ge=0, description="The number of questions the client has already received."),
    redis_client: redis.Redis = Depends(get_redis_client),
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
