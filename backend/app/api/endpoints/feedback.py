# backend/app/api/endpoints/feedback.py
"""
API Endpoints for User Feedback

Persists a user's thumbs-up/down rating and optional comment for a quiz result.
- Uses `verify_turnstile` to validate the Turnstile token from the request body.
- Commits the DB transaction on success; rolls back on failure.
- Ignores the Turnstile token when validating the payload model.
"""
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db_session, get_redis_client, verify_turnstile
from app.core.config import settings
from app.models.api import FeedbackRequest
from app.security.rate_limit import RateLimiter
from app.services.database import SessionRepository

router = APIRouter()
logger = structlog.get_logger(__name__)


async def _enforce_feedback_rate_limit(feedback: FeedbackRequest) -> None:
    """§9.7.4 — per-quiz feedback throttle (AC-FEEDBACK-RL-1..4).

    Buckets the limit by ``quiz_id`` so the same user can rate many quizzes
    but cannot spam a single one. Fails open on Redis errors.
    """
    fb_rl_cfg = getattr(settings.security, "feedback_rate_limit", None)
    if fb_rl_cfg is None or not fb_rl_cfg.enabled:
        return
    try:
        redis_client = get_redis_client()
        limiter = RateLimiter(
            redis=redis_client,
            capacity=fb_rl_cfg.capacity,
            refill_per_second=fb_rl_cfg.refill_per_second,
        )
        key = f"rl:feedback:{feedback.quiz_id}"
        res = await limiter.check(key)
    except HTTPException:
        raise
    except Exception:
        logger.warning("feedback.rate_limit.fail_open", exc_info=True)
        return
    if not res.allowed:
        logger.info(
            "feedback.rate_limited",
            session_id=str(feedback.quiz_id),
            retry_after=res.retry_after_s,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many feedback submissions for this quiz. Please slow down.",
            headers={
                "Retry-After": str(max(1, res.retry_after_s)),
                "X-RateLimit-Limit": str(fb_rl_cfg.capacity),
                "X-RateLimit-Remaining": "0",
            },
        )


@router.post(
    "/feedback",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Submit feedback for a quiz",
)
async def submit_feedback(
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    # Turnstile verified from the same raw JSON body; do not modify this dependency
    turnstile_verified: Annotated[bool, Depends(verify_turnstile)],
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

    # Validate against FeedbackRequest. Pydantic 2 is required (see
    # pyproject.toml), so model_validate is the only supported entry point.
    # On invalid input it raises pydantic.ValidationError, which FastAPI's
    # default exception handler maps to HTTP 422.
    feedback = FeedbackRequest.model_validate(body)

    await _enforce_feedback_rate_limit(feedback)

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
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not save feedback."
        ) from e
