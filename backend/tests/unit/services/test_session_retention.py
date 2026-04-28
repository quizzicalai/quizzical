"""§17.3 — Session retention helper (AC-SCALE-RETENTION-*)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import SessionHistory
from app.services.database import SessionRepository


pytestmark = pytest.mark.asyncio


async def _seed(
    session: AsyncSession, *, age_days: float, completed: bool = True
) -> uuid.UUID:
    sid = uuid.uuid4()
    obj = SessionHistory(
        session_id=sid,
        category=f"cat-{sid.hex[:6]}",
        category_synopsis={},
        session_transcript=[],
        is_completed=completed,
    )
    session.add(obj)
    await session.flush()
    # Override timestamps post-insert (server_default sets them initially).
    cutoff = datetime.now(timezone.utc) - timedelta(days=age_days)
    obj.last_updated_at = cutoff
    obj.created_at = cutoff
    if completed:
        obj.completed_at = cutoff
    await session.flush()
    return sid


async def test_purge_older_than_deletes_old_sessions(sqlite_db_session: AsyncSession):
    """AC-SCALE-RETENTION-1: rows older than ``days`` are removed."""
    repo = SessionRepository(sqlite_db_session)
    old_id = await _seed(sqlite_db_session, age_days=10)
    fresh_id = await _seed(sqlite_db_session, age_days=1)
    await sqlite_db_session.commit()

    deleted = await repo.purge_older_than(days=7)
    await sqlite_db_session.commit()

    assert deleted == 1
    assert await repo.get_by_id(old_id) is None
    assert await repo.get_by_id(fresh_id) is not None


async def test_purge_older_than_zero_match_returns_zero(sqlite_db_session: AsyncSession):
    """AC-SCALE-RETENTION-2: nothing matches → returns 0, no rows deleted."""
    repo = SessionRepository(sqlite_db_session)
    fresh_id = await _seed(sqlite_db_session, age_days=1)
    await sqlite_db_session.commit()

    deleted = await repo.purge_older_than(days=30)
    await sqlite_db_session.commit()

    assert deleted == 0
    assert await repo.get_by_id(fresh_id) is not None


async def test_purge_older_than_rejects_invalid_days(sqlite_db_session: AsyncSession):
    """AC-SCALE-RETENTION-3: days < 1 → ValueError, NO deletes performed."""
    repo = SessionRepository(sqlite_db_session)
    sid = await _seed(sqlite_db_session, age_days=100)
    await sqlite_db_session.commit()

    for bad in (0, -1, -100):
        with pytest.raises(ValueError):
            await repo.purge_older_than(days=bad)

    # Row is still present.
    assert await repo.get_by_id(sid) is not None


async def test_purge_older_than_uses_last_updated_at(sqlite_db_session: AsyncSession):
    """AC-SCALE-RETENTION-4: cutoff is by ``last_updated_at`` (most recent activity)."""
    repo = SessionRepository(sqlite_db_session)
    sid = uuid.uuid4()
    # Created long ago, but recently touched → should NOT be purged.
    obj = SessionHistory(
        session_id=sid,
        category="recent-touch",
        category_synopsis={},
        session_transcript=[],
        is_completed=False,
    )
    sqlite_db_session.add(obj)
    await sqlite_db_session.flush()
    obj.created_at = datetime.now(timezone.utc) - timedelta(days=60)
    obj.last_updated_at = datetime.now(timezone.utc) - timedelta(hours=1)
    await sqlite_db_session.commit()

    deleted = await repo.purge_older_than(days=7)
    await sqlite_db_session.commit()

    assert deleted == 0
    assert await repo.get_by_id(sid) is not None
