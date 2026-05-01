"""§21 Phase 7 — `v_topic_cost_30d` aggregation (`AC-PRECOMP-COST-4`)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models.db import PrecomputeJob, Topic
from app.services.precompute.cost import topic_cost_30d


async def _seed_topic(session, *, slug: str, name: str) -> Topic:
    t = Topic(id=uuid.uuid4(), slug=slug, display_name=name)
    session.add(t)
    await session.flush()
    return t


@pytest.mark.anyio
async def test_v_topic_cost_30d_aggregates_correctly(sqlite_db_session):
    s = sqlite_db_session
    a = await _seed_topic(s, slug="alpha", name="Alpha")
    b = await _seed_topic(s, slug="beta", name="Beta")
    now = datetime.now(tz=UTC)
    s.add_all([
        PrecomputeJob(
            id=uuid.uuid4(), topic_id=a.id, status="succeeded",
            cost_cents=12, created_at=now - timedelta(days=1),
        ),
        PrecomputeJob(
            id=uuid.uuid4(), topic_id=a.id, status="succeeded",
            cost_cents=8, created_at=now - timedelta(days=10),
        ),
        PrecomputeJob(
            id=uuid.uuid4(), topic_id=b.id, status="succeeded",
            cost_cents=5, created_at=now - timedelta(hours=2),
        ),
        # Outside window — must NOT be included.
        PrecomputeJob(
            id=uuid.uuid4(), topic_id=a.id, status="succeeded",
            cost_cents=99, created_at=now - timedelta(days=45),
        ),
    ])
    await s.commit()

    rows = await topic_cost_30d(s)
    by_slug = {r["slug"]: r["cost_cents"] for r in rows}
    assert by_slug == {"alpha": 20, "beta": 5}
    # Sorted descending.
    assert rows[0]["slug"] == "alpha"


@pytest.mark.anyio
async def test_v_topic_cost_30d_empty_when_no_jobs(sqlite_db_session):
    rows = await topic_cost_30d(sqlite_db_session)
    assert rows == []
