# backend/app/api/endpoints/results.py
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from app.models.api import ShareableResultResponse as ResultProfile 
from app.services.database import ResultService

# Create an API router for the results endpoint
router = APIRouter(
    prefix="/result",
    tags=["Results"]
)

@router.get(
    "/{result_id}",
    response_model=ResultProfile,
    summary="Get a quiz result by its ID",
    description="Retrieves the detailed character profile and results for a completed quiz session.",
)
async def get_result(
    result_id: UUID,
    result_service: ResultService = Depends(),
) -> ResultProfile:
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