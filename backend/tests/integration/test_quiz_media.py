# backend/tests/integration/test_quiz_media.py
"""
Integration tests for GET /quiz/{quiz_id}/media (AC-MEDIA-1..6).

These tests exercise the read-only media-snapshot endpoint that the FE polls
to surface asynchronously-generated FAL image URLs without blocking the
user-visible quiz flow.
"""

import uuid

import pytest

from app.main import API_PREFIX
from app.models.db import SessionHistory


def _media_url(quiz_id: uuid.UUID) -> str:
    return f"{API_PREFIX.rstrip('/')}/quiz/{quiz_id}/media"


@pytest.mark.anyio
@pytest.mark.usefixtures("override_db_dependency")
async def test_media_returns_empty_snapshot_when_no_row(client):
    """AC-MEDIA-1, AC-MEDIA-5, AC-MEDIA-6: Always 200 even when there is no row."""
    quiz_id = uuid.uuid4()
    resp = await client.get(_media_url(quiz_id))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {
        "quizId": str(quiz_id),
        "synopsisImageUrl": None,
        "resultImageUrl": None,
        "characters": [],
    }


@pytest.mark.anyio
@pytest.mark.usefixtures("override_db_dependency")
async def test_media_returns_persisted_urls(client, sqlite_db_session):
    """AC-MEDIA-2, AC-MEDIA-3, AC-MEDIA-4: Surfaces synopsis/character/result URLs."""
    quiz_id = uuid.uuid4()
    row = SessionHistory(
        session_id=quiz_id,
        category="Test Category",
        category_synopsis={
            "title": "T",
            "summary": "S",
            "image_url": "https://cdn/syn.jpg",
        },
        character_set=[
            {"name": "Alpha", "description": "a", "image_url": "https://cdn/a.jpg"},
            {"name": "Beta", "description": "b", "image_url": None},
        ],
        final_result={
            "title": "Winner",
            "description": "d",
            "image_url": "https://cdn/result.jpg",
        },
        session_transcript=[],
    )
    sqlite_db_session.add(row)
    await sqlite_db_session.commit()

    resp = await client.get(_media_url(quiz_id))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["quizId"] == str(quiz_id)
    assert body["synopsisImageUrl"] == "https://cdn/syn.jpg"
    assert body["resultImageUrl"] == "https://cdn/result.jpg"
    assert body["characters"] == [
        {"name": "Alpha", "imageUrl": "https://cdn/a.jpg"},
        {"name": "Beta", "imageUrl": None},
    ]


@pytest.mark.anyio
@pytest.mark.usefixtures("override_db_dependency")
async def test_media_dedupes_character_names(client, sqlite_db_session):
    """AC-MEDIA-3: Duplicate character names collapse to first occurrence."""
    quiz_id = uuid.uuid4()
    row = SessionHistory(
        session_id=quiz_id,
        category="Dupes",
        category_synopsis={"title": "T", "summary": "S"},
        character_set=[
            {"name": "Alpha", "image_url": "https://cdn/a1.jpg"},
            {"name": "Alpha", "image_url": "https://cdn/a2.jpg"},
            {"name": "  ", "image_url": "https://cdn/blank.jpg"},
            {"name": "Beta"},
        ],
        session_transcript=[],
    )
    sqlite_db_session.add(row)
    await sqlite_db_session.commit()

    resp = await client.get(_media_url(quiz_id))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    names = [c["name"] for c in body["characters"]]
    assert names == ["Alpha", "Beta"]
    assert body["characters"][0]["imageUrl"] == "https://cdn/a1.jpg"
    assert body["characters"][1]["imageUrl"] is None


@pytest.mark.anyio
@pytest.mark.usefixtures("override_db_dependency")
async def test_media_handles_null_jsonb_columns(client, sqlite_db_session):
    """AC-MEDIA-1: Null/empty JSONB columns produce a clean empty snapshot."""
    quiz_id = uuid.uuid4()
    row = SessionHistory(
        session_id=quiz_id,
        category="Empty",
        category_synopsis={},
        character_set=[],
        final_result=None,
        session_transcript=[],
    )
    sqlite_db_session.add(row)
    await sqlite_db_session.commit()

    resp = await client.get(_media_url(quiz_id))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["synopsisImageUrl"] is None
    assert body["resultImageUrl"] is None
    assert body["characters"] == []
