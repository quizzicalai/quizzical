# tests/unit/security/test_bounded_ids.py
"""§15.6 — Bounded ID lookups (AC-IDS-1..3)."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.database import CharacterRepository

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _repo() -> CharacterRepository:
    sess = MagicMock()
    sess.execute = AsyncMock()
    fake_result = MagicMock()
    fake_result.scalars.return_value.all.return_value = []
    sess.execute.return_value = fake_result
    return CharacterRepository(sess)


# AC-IDS-1
async def test_raises_when_over_max():
    repo = _repo()
    ids = [uuid.uuid4() for _ in range(101)]
    with pytest.raises(ValueError, match="exceeds maximum"):
        await repo.get_many_by_ids(ids)
    repo.session.execute.assert_not_awaited()


# AC-IDS-2
async def test_executes_at_max():
    repo = _repo()
    ids = [uuid.uuid4() for _ in range(100)]
    out = await repo.get_many_by_ids(ids)
    assert out == []
    repo.session.execute.assert_awaited_once()


# AC-IDS-3
async def test_empty_returns_no_sql():
    repo = _repo()
    out = await repo.get_many_by_ids([])
    assert out == []
    repo.session.execute.assert_not_awaited()
