# backend/tests/unit/models/test_db_models.py

import uuid
from datetime import datetime
from typing import List

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import (
    Character,
    SessionHistory,
    SessionQuestions,
    UserSentimentEnum,
    character_session_map,
)

# Ensure pytest-asyncio handles the async tests
pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Character Model Tests
# ---------------------------------------------------------------------------

async def test_character_creation_happy_path(sqlite_db_session: AsyncSession):
    """
    Verify a Character can be created with required fields and UUID is generated.
    """
    char = Character(
        name="The Optimist",
        short_description="Always sees the bright side.",
        profile_text="Detailed profile text goes here.",
    )
    sqlite_db_session.add(char)
    await sqlite_db_session.commit()
    await sqlite_db_session.refresh(char)

    assert isinstance(char.id, uuid.UUID)
    assert char.name == "The Optimist"
    assert isinstance(char.created_at, datetime)
    assert isinstance(char.last_updated_at, datetime)
    # Check default None fields
    assert char.profile_picture is None
    assert char.judge_quality_score is None


async def test_character_constraints_empty_strings(sqlite_db_session: AsyncSession):
    """
    Verify constraints: name, short_description, and profile_text cannot be empty strings.
    """
    # Case 1: Empty name
    char1 = Character(name="", short_description="desc", profile_text="profile")
    sqlite_db_session.add(char1)
    with pytest.raises(IntegrityError):
        await sqlite_db_session.commit()
    await sqlite_db_session.rollback()

    # Case 2: Empty short_description
    char2 = Character(name="Valid", short_description="", profile_text="profile")
    sqlite_db_session.add(char2)
    with pytest.raises(IntegrityError):
        await sqlite_db_session.commit()
    await sqlite_db_session.rollback()


async def test_character_judge_score_constraint(sqlite_db_session: AsyncSession):
    """
    Verify judge_quality_score constraint (1-10).
    """
    # Valid
    char_ok = Character(name="Ok", short_description=".", profile_text=".", judge_quality_score=10)
    sqlite_db_session.add(char_ok)
    await sqlite_db_session.commit()

    # Invalid (0)
    char_low = Character(name="Low", short_description=".", profile_text=".", judge_quality_score=0)
    sqlite_db_session.add(char_low)
    with pytest.raises(IntegrityError):
        await sqlite_db_session.commit()
    await sqlite_db_session.rollback()

    # Invalid (11)
    char_high = Character(name="High", short_description=".", profile_text=".", judge_quality_score=11)
    sqlite_db_session.add(char_high)
    with pytest.raises(IntegrityError):
        await sqlite_db_session.commit()


# ---------------------------------------------------------------------------
# SessionHistory Model Tests
# ---------------------------------------------------------------------------

async def test_session_history_creation_defaults(sqlite_db_session: AsyncSession):
    """
    Verify SessionHistory creation and server defaults (JSONB, Booleans).
    """
    sid = uuid.uuid4()
    session = SessionHistory(
        session_id=sid,
        category="Cats",
        category_synopsis={"title": "Cats Quiz", "summary": "..."},
        session_transcript=[],
        is_completed=False  # Explicitly set to avoid SQLite server_default ambiguity
    )
    sqlite_db_session.add(session)
    await sqlite_db_session.commit()
    await sqlite_db_session.refresh(session)

    assert session.session_id == sid
    assert session.is_completed is False
    
    # Check default for character_set (list)
    # In SQLite with our fixture, ::jsonb is stripped, so it enters as '[]'.
    assert session.character_set == [] 


async def test_session_history_json_persistence(sqlite_db_session: AsyncSession):
    """
    Verify complex JSON persistence for transcript and synopsis.
    """
    transcript = [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello"}
    ]
    session = SessionHistory(
        session_id=uuid.uuid4(),
        category="Test",
        category_synopsis={"meta": "data"},
        session_transcript=transcript,
        agent_plan={"steps": ["step1", "step2"]},
        is_completed=False
    )
    sqlite_db_session.add(session)
    await sqlite_db_session.commit()
    await sqlite_db_session.refresh(session)

    assert session.session_transcript == transcript
    assert session.agent_plan == {"steps": ["step1", "step2"]}


async def test_session_vector_field_mocking(sqlite_db_session: AsyncSession):
    """
    Verify the vector column works (mocked as TEXT/JSON via fixtures in SQLite).
    """
    # Create a vector with exactly 384 dimensions (pgvector requirement)
    vec = [0.1] * 384 
    
    session = SessionHistory(
        session_id=uuid.uuid4(),
        category="VectorTest",
        category_synopsis={},
        session_transcript=[],
        synopsis_embedding=vec,
        is_completed=False
    )
    sqlite_db_session.add(session)
    await sqlite_db_session.commit()
    await sqlite_db_session.refresh(session)
    
    # Fix: Ensure we compare lists to lists to avoid numpy/pgvector ambiguous boolean errors
    stored_vec = session.synopsis_embedding
    if hasattr(stored_vec, "tolist"):  # Handle numpy array if present
        stored_vec = stored_vec.tolist()
    
    # Fix: Use pytest.approx to handle float precision differences
    assert stored_vec == pytest.approx(vec)


async def test_session_enums_and_completion(sqlite_db_session: AsyncSession):
    """
    Verify Enum storage and boolean completion flags.
    """
    session = SessionHistory(
        session_id=uuid.uuid4(),
        category="EnumTest",
        category_synopsis={},
        session_transcript=[],
        user_sentiment=UserSentimentEnum.POSITIVE,
        is_completed=True,
        completed_at=datetime.utcnow()
    )
    sqlite_db_session.add(session)
    await sqlite_db_session.commit()
    await sqlite_db_session.refresh(session)

    assert session.user_sentiment == UserSentimentEnum.POSITIVE
    assert session.is_completed is True
    assert isinstance(session.completed_at, datetime)


# ---------------------------------------------------------------------------
# Relationships (Session <-> Character)
# ---------------------------------------------------------------------------

async def test_many_to_many_relationship(sqlite_db_session: AsyncSession):
    """
    Verify that Characters can be associated with Sessions via the secondary table.
    """
    # Create Session
    sess = SessionHistory(
        session_id=uuid.uuid4(),
        category="M2M",
        category_synopsis={},
        session_transcript=[],
        is_completed=False
    )
    
    # Create Characters
    c1 = Character(name="C1", short_description=".", profile_text=".")
    c2 = Character(name="C2", short_description=".", profile_text=".")
    
    sqlite_db_session.add_all([sess, c1, c2])
    
    # Associate
    sess.characters.append(c1)
    sess.characters.append(c2)
    
    await sqlite_db_session.commit()
    
    # Refresh and verify reverse
    await sqlite_db_session.refresh(c1, attribute_names=["sessions"])
    assert len(c1.sessions) == 1
    assert c1.sessions[0].session_id == sess.session_id

    # Verify forward via direct query
    count_stmt = select(func.count()).select_from(character_session_map).where(
        character_session_map.c.session_id == sess.session_id
    )
    count = (await sqlite_db_session.execute(count_stmt)).scalar()
    assert count == 2


async def test_cascade_delete_session_clears_map(sqlite_db_session: AsyncSession):
    """
    Verify that deleting a Session removes entries from the character_session_map
    but DOES NOT delete the Character.
    """
    sess = SessionHistory(
        session_id=uuid.uuid4(),
        category="DeleteTest",
        category_synopsis={},
        session_transcript=[],
        is_completed=False
    )
    char = Character(name="Survivor", short_description=".", profile_text=".")
    sess.characters.append(char)
    sqlite_db_session.add(sess)
    await sqlite_db_session.commit()

    # Delete Session
    await sqlite_db_session.delete(sess)
    await sqlite_db_session.commit()

    # Verify Map is empty for this session
    map_count = (await sqlite_db_session.execute(
        select(func.count()).where(character_session_map.c.session_id == sess.session_id)
    )).scalar()
    assert map_count == 0

    # Verify Character still exists
    char_exists = (await sqlite_db_session.execute(
        select(Character).where(Character.id == char.id)
    )).scalar_one_or_none()
    assert char_exists is not None


# ---------------------------------------------------------------------------
# SessionQuestions Model Tests
# ---------------------------------------------------------------------------

async def test_session_questions_linkage(sqlite_db_session: AsyncSession):
    """
    Verify SessionQuestions links 1:1 to SessionHistory and handles JSON blobs.
    """
    sid = uuid.uuid4()
    sess = SessionHistory(
        session_id=sid,
        category="QLink",
        category_synopsis={},
        session_transcript=[],
        is_completed=False
    )
    sqlite_db_session.add(sess)
    
    questions = SessionQuestions(
        session_id=sid,
        baseline_questions={"q": [1, 2]},
        adaptive_questions={"q": [3]},
        properties={"source": "test"}
    )
    sqlite_db_session.add(questions)
    await sqlite_db_session.commit()

    # Retrieve
    retrieved = await sqlite_db_session.get(SessionQuestions, sid)
    assert retrieved is not None
    assert retrieved.baseline_questions == {"q": [1, 2]}
    assert retrieved.properties["source"] == "test"


async def test_session_questions_cascade_delete(sqlite_db_session: AsyncSession):
    """
    Verify deletion of SessionHistory cascades to SessionQuestions.

    Important: use an explicit SELECT query instead of `session.get()` so we
    actually hit the database and don't get tricked by the session identity map.
    """
    sid = uuid.uuid4()

    # Create a SessionHistory row and its associated SessionQuestions row
    sess = SessionHistory(
        session_id=sid,
        category="CascadeQ",
        category_synopsis={},
        session_transcript=[],
        is_completed=False,
    )
    questions = SessionQuestions(session_id=sid, baseline_questions={})

    sqlite_db_session.add(sess)
    sqlite_db_session.add(questions)
    await sqlite_db_session.commit()

    # Delete the parent SessionHistory; SQLite should cascade to SessionQuestions
    await sqlite_db_session.delete(sess)
    await sqlite_db_session.commit()

    # Verify Questions are gone by querying the DB directly (not via session.get)
    result = await sqlite_db_session.execute(
        select(SessionQuestions).where(SessionQuestions.session_id == sid)
    )
    orphaned = result.scalar_one_or_none()
    assert orphaned is None