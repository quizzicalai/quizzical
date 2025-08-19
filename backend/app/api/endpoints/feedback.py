"""
API Endpoints for User Feedback

This module contains the FastAPI route for submitting user feedback on a
completed quiz result.
"""
import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db_session, verify_turnstile
from app.models.api import FeedbackRequest
from app.services.database import SessionRepository

router = APIRouter()
logger = structlog.get_logger(__name__)


@router.post(
    "/feedback",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Submit feedback for a quiz",
)
async def submit_feedback(
    request: FeedbackRequest, 
    db: AsyncSession = Depends(get_db_session),
    # The verify_turnstile dependency is added here to protect the endpoint
    turnstile_verified: bool = Depends(verify_turnstile),
):
    """
    Submits user feedback (a rating and optional text) on a completed quiz result.
    """
    session_repo = SessionRepository(db)
    session_id_str = str(request.quiz_id)

    logger.info(
        "Received feedback submission",
        session_id=session_id_str,
        rating=request.rating.value,
        has_text=bool(request.text),
    )

    updated_session = await session_repo.save_feedback(
        session_id=request.quiz_id,
        rating=request.rating,
        feedback_text=request.text,
    )

    if not updated_session:
        logger.warning("Feedback submission failed: session not found", session_id=session_id_str)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Quiz session not found.",
        )

    logger.info("Feedback successfully saved", session_id=session_id_str)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
