# backend/tests/unit/services/test_database.py

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.api import FeedbackRatingEnum, ShareableResultResponse
from app.models.db import (
    Character,
    SessionHistory,
    SessionQuestions,
    UserSentimentEnum,
    character_session_map,
)
from app.services.database import (
    CharacterRepository,
    ResultService,
    SessionQuestionsRepository,
    SessionRepository,
    _omit_none,
    normalize_final_result,
)

# Use the async mark for all tests in this module
pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helper Tests
# ---------------------------------------------------------------------------

def test_omit_none():
    """Test the helper checks for None values."""
    data = {"a": 1, "b": None, "c": "test"}
    result = _omit_none(data)
    assert result == {"a": 1, "c": "test"}
    assert "b" not in result
    # Edge case: None input
    assert _omit_none(None) == {}


def test_normalize_final_result_variants():
    """Test normalization logic for different result structures."""
    # 1. None
    assert normalize_final_result(None) is None

    # 2. Pydantic-like object (with model_dump)
    class MockPydanticV2:
        def model_dump(self):
            return {"title": "V2", "description": "Desc"}
    
    res = normalize_final_result(MockPydanticV2())
    assert res["title"] == "V2"

    # 3. Dataclass-like object (with dict)
    class MockPydanticV1:
        def dict(self):
            return {"title": "V1", "description": "Desc"}
    
    res = normalize_final_result(MockPydanticV1())
    assert res["title"] == "V1"

    # 4. String
    res = normalize_final_result("Just text")
    assert res["title"] == "Quiz Result"
    assert res["description"] == "Just text"

    # 5. Dict with aliases (frontend camelCase vs backend snake_case)
    res = normalize_final_result({"profileTitle": "Camel", "summary": "Sum", "imageUrl": "http://img"})
    assert res["title"] == "Camel"
    assert res["description"] == "Sum"
    assert res["image_url"] == "http://img"

    # 6. Unsupported type
    assert normalize_final_result(12345) is None


# ---------------------------------------------------------------------------
# CharacterRepository Tests
# ---------------------------------------------------------------------------

async def test_char_repo_get_by_id(sqlite_db_session: AsyncSession):
    repo = CharacterRepository(sqlite_db_session)
    
    # Create seed with ALL required fields
    char = await repo.create(
        name="Finder", 
        short_description="Searcher", 
        profile_text="Deep profile text"
    )
    
    # Found
    fetched = await repo.get_by_id(char.id)
    assert fetched is not None
    assert fetched.name == "Finder"

    # Not found
    assert await repo.get_by_id(uuid.uuid4()) is None


async def test_char_repo_get_many_by_ids(sqlite_db_session: AsyncSession):
    repo = CharacterRepository(sqlite_db_session)
    # Provide required fields for create
    c1 = await repo.create(name="A", short_description="d", profile_text="p")
    c2 = await repo.create(name="B", short_description="d", profile_text="p")
    c3 = await repo.create(name="C", short_description="d", profile_text="p")

    # Empty list edge case
    assert await repo.get_many_by_ids([]) == []

    # Fetch subset
    results = await repo.get_many_by_ids([c1.id, c3.id])
    assert len(results) == 2
    names = {c.name for c in results}
    assert "A" in names and "C" in names


async def test_char_repo_upsert_by_name(sqlite_db_session: AsyncSession):
    """Verify create-or-update logic."""
    repo = CharacterRepository(sqlite_db_session)

    # 1. Create new
    char = await repo.upsert_by_name(name="UniqueOne", short_description="Desc 1", profile_text="Profile 1")
    assert char.id is not None
    assert char.short_description == "Desc 1"

    # 2. Update existing (same name)
    char_updated = await repo.upsert_by_name(name="UniqueOne", short_description="Desc 2", profile_text="Profile 2")
    
    # [FIX] Force refresh to ensure we see DB state.
    # In SQLite/SQLAlchemy test environments, 'RETURNING' on upserts might not 
    # automatically sync the Identity Map for objects already in the session.
    await sqlite_db_session.refresh(char_updated)

    assert char_updated.id == char.id  # Same UUID
    assert char_updated.short_description == "Desc 2"


async def test_char_repo_update_profile(sqlite_db_session: AsyncSession):
    repo = CharacterRepository(sqlite_db_session)
    char = await repo.create(name="ProfileTester", short_description="d", profile_text="Old")
    
    # Happy path
    updated = await repo.update_profile(char.id, "New Profile Text")
    assert updated.profile_text == "New Profile Text"
    
    # Not found
    assert await repo.update_profile(uuid.uuid4(), "...") is None


async def test_char_repo_set_profile_picture(sqlite_db_session: AsyncSession):
    repo = CharacterRepository(sqlite_db_session)
    char = await repo.create(name="PicTester", short_description="d", profile_text="p")
    
    # Happy path
    success = await repo.set_profile_picture(char.id, b"fake_image_bytes")
    assert success is True
    
    # Verify DB
    await sqlite_db_session.refresh(char)
    assert char.profile_picture == b"fake_image_bytes"

    # Not found
    assert await repo.set_profile_picture(uuid.uuid4(), b"") is False


# ---------------------------------------------------------------------------
# SessionRepository Tests
# ---------------------------------------------------------------------------

async def test_session_repo_get_by_id(sqlite_db_session: AsyncSession):
    repo = SessionRepository(sqlite_db_session)
    
    # Create directly via model to seed
    sid = uuid.uuid4()
    obj = SessionHistory(session_id=sid, category="Test", category_synopsis={}, session_transcript=[])
    sqlite_db_session.add(obj)
    await sqlite_db_session.commit()

    # Test Get
    res = await repo.get_by_id(sid)
    assert res is not None
    assert res.category == "Test"
    
    # Not found
    assert await repo.get_by_id(uuid.uuid4()) is None


async def test_session_repo_upsert_logic_and_linking(sqlite_db_session: AsyncSession):
    """
    Complex test: Upsert session, create characters on the fly, ensure links.
    """
    repo = SessionRepository(sqlite_db_session)
    sid = uuid.uuid4()
    
    # Provide full character details to satisfy DB constraints
    chars_payload = [
        {"name": "Char A", "short_description": "Alpha", "profile_text": "P1"},
        {"name": "Char B", "short_description": "Beta", "profile_text": "P2"},
        {"name": ""}, # Edge case: Empty name should be skipped
    ]

    # 1. Initial Insert
    session_obj = await repo.upsert_session_after_synopsis(
        session_id=sid,
        category="Complex",
        synopsis_dict={"summary": "init"},
        transcript=[],
        characters_payload=chars_payload,
        agent_plan={"step": 1}
    )

    assert session_obj.category == "Complex"
    assert session_obj.agent_plan == {"step": 1}

    # Verify Characters were created
    # Using direct select to bypass upsert defaults logic in test check
    result = await sqlite_db_session.execute(select(Character).where(Character.name == "Char A"))
    char_a = result.scalars().first()
    
    assert char_a is not None
    assert char_a.short_description == "Alpha"

    # Verify Linking (M:N)
    link_count = await sqlite_db_session.execute(
        select(func.count()).select_from(character_session_map).where(
            character_session_map.c.session_id == sid
        )
    )
    assert link_count.scalar() == 2  # Char A and Char B

    # 2. Update Session (idempotency check)
    # Should update synopsis, keep characters if not passed, or add new ones if passed.
    # Here we pass None for characters, ensuring no new links but session update.
    updated_obj = await repo.upsert_session_after_synopsis(
        session_id=sid,
        category="Complex",
        synopsis_dict={"summary": "updated"},
        transcript=[{"role": "user"}],
        agent_plan={"step": 2}
    )
    assert updated_obj.category_synopsis["summary"] == "updated"
    assert len(updated_obj.session_transcript) == 1


async def test_session_repo_mark_completed(sqlite_db_session: AsyncSession):
    repo = SessionRepository(sqlite_db_session)
    sid = uuid.uuid4()
    # Seed
    await repo.upsert_session_after_synopsis(
        session_id=sid, category="C", synopsis_dict={}, transcript=[]
    )

    # Mark Complete
    success = await repo.mark_completed(
        session_id=sid,
        final_result={"title": "Done"},
        qa_history=[{"q": "a"}]
    )
    assert success is True

    # Verify
    obj = await repo.get_by_id(sid)
    assert obj.is_completed is True
    assert obj.final_result["title"] == "Done"
    assert obj.qa_history[0]["q"] == "a"


async def test_session_repo_update_qa_history(sqlite_db_session: AsyncSession):
    repo = SessionRepository(sqlite_db_session)
    sid = uuid.uuid4()
    await repo.upsert_session_after_synopsis(
        session_id=sid, category="QA", synopsis_dict={}, transcript=[]
    )

    # Update QA
    success = await repo.update_qa_history(session_id=sid, qa_history=[{"q1": "ans1"}])
    assert success is True

    obj = await repo.get_by_id(sid)
    assert obj.qa_history == [{"q1": "ans1"}]
    assert obj.is_completed is False  # Should remain incomplete


async def test_session_repo_save_feedback(sqlite_db_session: AsyncSession):
    repo = SessionRepository(sqlite_db_session)
    sid = uuid.uuid4()
    # Seed
    await repo.upsert_session_after_synopsis(
        session_id=sid, category="F", synopsis_dict={}, transcript=[]
    )

    # 1. Upvote
    updated = await repo.save_feedback(sid, FeedbackRatingEnum.UP, "Great job")
    assert updated.user_sentiment == UserSentimentEnum.POSITIVE
    assert updated.user_feedback_text == "Great job"

    # 2. Downvote
    updated = await repo.save_feedback(sid, FeedbackRatingEnum.DOWN, "Bad job")
    assert updated.user_sentiment == UserSentimentEnum.NEGATIVE

    # 3. Non-existent session
    res = await repo.save_feedback(uuid.uuid4(), FeedbackRatingEnum.UP, None)
    assert res is None


# ---------------------------------------------------------------------------
# SessionQuestionsRepository Tests
# ---------------------------------------------------------------------------

async def test_questions_repo_upsert_baseline(sqlite_db_session: AsyncSession):
    repo = SessionQuestionsRepository(sqlite_db_session)
    
    # Need a parent session first (Foreign Key constraint)
    s_repo = SessionRepository(sqlite_db_session)
    sid = uuid.uuid4()
    await s_repo.upsert_session_after_synopsis(
        session_id=sid, category="Q", synopsis_dict={}, transcript=[]
    )

    # 1. Create
    q_obj = await repo.upsert_baseline(
        session_id=sid,
        baseline_blob={"q": [1]},
        properties={"v": 1}
    )
    assert q_obj.baseline_questions == {"q": [1]}
    
    # 2. Update
    q_obj_2 = await repo.upsert_baseline(
        session_id=sid,
        baseline_blob={"q": [1, 2]},
        properties={"v": 2}
    )
    
    # [FIX] Force refresh to ensure we see DB state.
    await sqlite_db_session.refresh(q_obj_2)
    
    assert q_obj_2.baseline_questions == {"q": [1, 2]}
    assert q_obj_2.properties["v"] == 2


async def test_questions_repo_upsert_adaptive(sqlite_db_session: AsyncSession):
    repo = SessionQuestionsRepository(sqlite_db_session)
    s_repo = SessionRepository(sqlite_db_session)
    sid = uuid.uuid4()
    await s_repo.upsert_session_after_synopsis(
        session_id=sid, category="Q", synopsis_dict={}, transcript=[]
    )

    # Insert adaptive (creates row if not exists)
    q_obj = await repo.upsert_adaptive(
        session_id=sid,
        adaptive_blob={"a": [1]},
        properties={"step": "adaptive"}
    )
    assert q_obj.adaptive_questions == {"a": [1]}
    assert q_obj.session_id == sid


async def test_questions_repo_baseline_exists(sqlite_db_session: AsyncSession):
    repo = SessionQuestionsRepository(sqlite_db_session)
    s_repo = SessionRepository(sqlite_db_session)
    sid = uuid.uuid4()
    await s_repo.upsert_session_after_synopsis(
        session_id=sid, category="Q", synopsis_dict={}, transcript=[]
    )

    # Initially False
    assert await repo.baseline_exists(sid) is False

    # Insert
    await repo.upsert_baseline(session_id=sid, baseline_blob={"data": "yes"})
    
    # Now True
    assert await repo.baseline_exists(sid) is True


async def test_questions_repo_get_for_session(sqlite_db_session: AsyncSession):
    repo = SessionQuestionsRepository(sqlite_db_session)
    
    # Not found
    assert await repo.get_for_session(uuid.uuid4()) is None

    # Found
    s_repo = SessionRepository(sqlite_db_session)
    sid = uuid.uuid4()
    await s_repo.upsert_session_after_synopsis(session_id=sid, category="Q", synopsis_dict={}, transcript=[])
    await repo.upsert_baseline(session_id=sid, baseline_blob={})
    
    res = await repo.get_for_session(sid)
    assert res is not None
    assert isinstance(res, SessionQuestions)


# ---------------------------------------------------------------------------
# ResultService Tests
# ---------------------------------------------------------------------------

async def test_result_service_get_result_by_id(sqlite_db_session: AsyncSession):
    service = ResultService(sqlite_db_session)
    
    # 1. Not Found
    assert await service.get_result_by_id(uuid.uuid4()) is None

    # 2. Found but not completed (no final_result)
    s_repo = SessionRepository(sqlite_db_session)
    sid = uuid.uuid4()
    await s_repo.upsert_session_after_synopsis(
        session_id=sid, category="R", synopsis_dict={}, transcript=[]
    )
    assert await service.get_result_by_id(sid) is None

    # 3. Found and completed
    await s_repo.mark_completed(
        session_id=sid,
        final_result={"title": "Winner", "description": "You won", "imageUrl": "http://win.jpg"}
    )
    
    res = await service.get_result_by_id(sid)
    assert isinstance(res, ShareableResultResponse)
    assert res.title == "Winner"
    assert res.image_url == "http://win.jpg"
    assert res.category == "R"
    assert res.created_at is not None  # Should be serialized string