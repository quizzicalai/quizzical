"""
API Endpoints for Serving Assets

This module contains the FastAPI route for serving persisted binary assets,
such as the generated character profile pictures.
"""
import hashlib
import uuid
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db_session
from app.services.database import CharacterRepository

router = APIRouter()
logger = structlog.get_logger(__name__)


@router.get(
    "/character/{character_id}/image",
    summary="Get character profile image",
    # Use a custom response class to prevent FastAPI from adding a default
    # "application/json" content-type to the OpenAPI docs.
    response_class=Response,
    responses={
        200: {"content": {"image/png": {}}, "description": "The character's profile image."},
        304: {"description": "Not Modified. The client's cached version is up-to-date."},
        404: {"description": "Character or image not found"},
    },
)
async def get_character_image(
    character_id: uuid.UUID,
    if_none_match: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db_session),
):
    """
    Serves the persisted binary image data for a character's profile picture.

    This endpoint implements a complete browser caching strategy using ETag
    and Cache-Control headers, including handling the `If-None-Match` header
    to return a 304 Not Modified response when the client's cache is current.
    """
    char_repo = CharacterRepository(db)
    character = await char_repo.get_by_id(character_id)

    if not character or not character.profile_picture:
        logger.warning("Character image not found", character_id=str(character_id))
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Character or image not found.",
        )

    image_bytes = character.profile_picture
    etag = f'"{hashlib.md5(image_bytes).hexdigest()}"'

    # Check if the client's cached ETag matches the current ETag.
    if if_none_match == etag:
        # If they match, the client's image is up-to-date.
        # Return a 304 Not Modified response with no body.
        return Response(status_code=status.HTTP_304_NOT_MODIFIED)

    # Define caching headers for the full response
    headers = {
        "ETag": etag,
        "Cache-Control": "public, max-age=31536000, immutable",
    }

    logger.info("Serving character image", character_id=str(character_id))
    return Response(content=image_bytes, media_type="image/png", headers=headers)
