# backend/app/api/endpoints/feedback.py
"""
API Endpoints for User Feedback

Persists a user's thumbs-up/down rating and optional comment for a quiz result.
- Uses `verify_turnstile` to validate the Turnstile token from the request body.
- Commits the DB transaction on success; rolls back on failure.
- Ignores the Turnstile token when validating the payload model.
"""
import structlog
from fastapi import APIRouter, Depends, HTTPException, Response, Request, status
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
    request: Request,
    db: AsyncSession = Depends(get_db_session),
    # Turnstile verified from the same raw JSON body; do not modify this dependency
    turnstile_verified: bool = Depends(verify_turnstile),
):
    """
    Submits user feedback (rating + optional text) and persists it.
    """
    # Parse the raw JSON body (Starlette caches the body; ok to read after dependency).
    try:
        body = await request.json()
    except Exception:
        body = {}

    # Remove Turnstile token before Pydantic validation; it's not part of FeedbackRequest.
    body.pop("cf-turnstile-response", None)

    # Validate against FeedbackRequest (support Pydantic v2 then v1 as a fallback).
    try:  # Pydantic v2
        feedback = FeedbackRequest.model_validate(body)
    except AttributeError:  # Pydantic v1
        feedback = FeedbackRequest.parse_obj(body)

    session_repo = SessionRepository(db)
    session_id_str = str(feedback.quiz_id)

    # Normalize empty/whitespace-only comment to NULL
    comment = feedback.text.strip() if isinstance(feedback.text, str) else feedback.text
    if comment == "":
        comment = None

    logger.info(
        "feedback.submit.start",
        session_id=session_id_str,
        rating=getattr(feedback.rating, "value", str(feedback.rating)),
        has_text=bool(comment),
    )

    try:
        updated_session = await session_repo.save_feedback(
            session_id=feedback.quiz_id,
            rating=feedback.rating,
            feedback_text=comment,
        )

        if not updated_session:
            logger.warning("feedback.submit.missing_session", session_id=session_id_str)
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Quiz session not found.")

        # ✅ Persist the update
        await db.commit()

        logger.info("feedback.submit.ok", session_id=session_id_str)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    except HTTPException:
        # Let FastAPI handle deliberate HTTP errors
        raise
    except Exception as e:
        logger.error("feedback.submit.error", session_id=session_id_str, error=str(e), exc_info=True)
        try:
            await db.rollback()
        except Exception:
            logger.warning("feedback.submit.rollback_failed", session_id=session_id_str, exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Could not save feedback.")
