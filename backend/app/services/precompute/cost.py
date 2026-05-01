"""§21 Phase 7 — cost attribution (`AC-PRECOMP-COST-4`).

`v_topic_cost_30d` aggregates `precompute_jobs.cost_cents` over the last
30 days per `topic_id`. On Postgres a real SQL view ships in
`db/init/init.sql`; on SQLite the function below executes the equivalent
GROUP BY at request time so tests stay portable.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import PrecomputeJob, Topic


async def topic_cost_30d(session: AsyncSession) -> list[dict[str, object]]:
    """Return `[{topic_id, slug, display_name, cost_cents}, ...]` for
    jobs whose `created_at` falls inside the trailing 30-day window.

    Result is sorted by descending `cost_cents` so the operator UI lists
    the most expensive topics first."""
    cutoff = datetime.now(tz=UTC) - timedelta(days=30)
    rows = (
        await session.execute(
            select(
                Topic.id,
                Topic.slug,
                Topic.display_name,
                func.coalesce(func.sum(PrecomputeJob.cost_cents), 0).label("cost_cents"),
            )
            .join(PrecomputeJob, PrecomputeJob.topic_id == Topic.id)
            .where(PrecomputeJob.created_at >= cutoff)
            .group_by(Topic.id, Topic.slug, Topic.display_name)
            .order_by(func.sum(PrecomputeJob.cost_cents).desc())
        )
    ).all()
    return [
        {
            "topic_id": str(r[0]),
            "slug": r[1],
            "display_name": r[2],
            "cost_cents": int(r[3] or 0),
        }
        for r in rows
    ]
