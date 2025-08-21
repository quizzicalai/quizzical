"""
API Endpoint for retrieving quiz results.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.models.api import ShareableResultResponse
# FIX: The ResultService is now correctly defined in the database service module.
from app.services.database import ResultService

# Create an API router for the results endpoint
router = APIRouter(
    prefix="/result",
    tags=["Results"]
)

@router.get(
    "/{result_id}",
    response_model=ShareableResultResponse,
    summary="Get a quiz result by its ID",
    description="Retrieves the detailed character profile and results for a completed quiz session.",
)
async def get_result(
    result_id: UUID,
    # FIX: This dependency now works because ResultService's __init__
    # is compatible with FastAPI's dependency injection.
    result_service: ResultService = Depends(ResultService),
) -> ShareableResultResponse:
    """
    Handles the retrieval of a quiz result.

    - **result_id**: The unique identifier for the quiz result.
    - **result_service**: Dependency injection for the result service.

    Returns the result profile or raises a 404 error if not found.
    """
    result = await result_service.get_result_by_id(result_id)
    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Result not found. It may have expired or never existed.",
        )
    return result
