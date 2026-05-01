"""§21 Phase 4 — `mv_topic_pack_resolved` refresh helper.

The MV holds one resolved row per published topic pack. After `publish()`
performs the atomic swap (Phase 3), it must `REFRESH MATERIALIZED VIEW
CONCURRENTLY mv_topic_pack_resolved` so subsequent reads see the new
pack_id. Concurrent refresh requires a unique index on the MV (the
init.sql migration creates one on `topic_id`).

The helper is a no-op on non-Postgres dialects (the SQLite test bench
falls back to a plain JOIN inside the resolver), keeping the rest of the
publish path portable.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger("app.services.precompute.mv_refresh")

MV_NAME = "mv_topic_pack_resolved"
REFRESH_SQL = f"REFRESH MATERIALIZED VIEW CONCURRENTLY {MV_NAME}"


def _is_postgres(session: AsyncSession) -> bool:
    bind = session.get_bind()
    name = getattr(getattr(bind, "dialect", None), "name", "")
    return str(name).lower().startswith("postgres")


async def refresh_mv_topic_pack_resolved(session: AsyncSession) -> bool:
    """Refresh the resolver MV concurrently.

    Returns True when the refresh ran (Postgres), False on other dialects
    (SQLite test bench). Best-effort: a refresh failure is logged but
    never propagates — the cached pack invalidation in the same publish
    transaction is the user-visible correctness guarantee.
    """
    if not _is_postgres(session):
        return False
    try:
        await session.execute(text(REFRESH_SQL))
        return True
    except Exception:  # noqa: BLE001 — best-effort; log + continue
        logger.warning("precompute.mv_refresh.failed", exc_info=True)
        return False
