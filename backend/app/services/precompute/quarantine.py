"""§21 Phase 6 — auto-quarantine + cascade.

Two entry points:

- `quarantine_pack(session, pack_id)` — flips a single TopicPack to
  `status='quarantined'`. Idempotent. Returns True when the row was
  actually mutated (so callers can avoid spurious cache invalidations
  on no-op calls).
- `cascade_quarantine_for_character(session, character_id)` — when a
  character's evaluator score drops below `τ_pass`, every published
  pack whose `character_set_id` references the character set containing
  this character is quarantined in the same transaction
  (`AC-PRECOMP-SEC-6`).

Cache invalidation is the caller's responsibility — these helpers stay
DB-pure so they compose cleanly with the existing transaction in
`flag_content`. The endpoint layer calls `cache.invalidate_pack` after
commit.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import CharacterSet, TopicPack

QUARANTINED = "quarantined"
PUBLISHED = "published"


async def quarantine_pack(session: AsyncSession, pack_id: UUID | str) -> bool:
    """Flip `pack_id` to `quarantined`. Returns True on actual mutation.

    Already-quarantined packs are a no-op (returns False) so we don't
    spam audit/cache layers."""
    row = (
        await session.execute(select(TopicPack).where(TopicPack.id == pack_id))
    ).scalar_one_or_none()
    if row is None or row.status == QUARANTINED:
        return False
    row.status = QUARANTINED
    session.add(row)
    return True


async def cascade_quarantine_for_character(
    session: AsyncSession, character_id: UUID | str
) -> list[UUID]:
    """Quarantine every published pack whose character_set composition
    references `character_id`. Returns the list of pack ids mutated.

    Membership lives in `character_sets.composition` JSON
    (`{"character_ids": [...]}`); we scan in Python so the helper works
    identically on Postgres and the SQLite test bench."""
    cid_str = str(character_id)
    sets = (await session.execute(select(CharacterSet))).scalars().all()
    matching_set_ids: list[UUID] = []
    for cs in sets:
        comp = cs.composition or {}
        ids = comp.get("character_ids") if isinstance(comp, dict) else None
        if isinstance(ids, list) and any(str(x) == cid_str for x in ids):
            matching_set_ids.append(cs.id)
    if not matching_set_ids:
        return []
    affected = (
        await session.execute(
            select(TopicPack).where(
                TopicPack.character_set_id.in_(matching_set_ids),
                TopicPack.status == PUBLISHED,
            )
        )
    ).scalars().all()
    mutated: list[UUID] = []
    for pack in affected:
        pack.status = QUARANTINED
        session.add(pack)
        mutated.append(pack.id)
    return mutated


async def bulk_set_pack_status(
    session: AsyncSession, pack_ids: list[UUID | str], *, status: str
) -> int:
    """Bulk status update — used by the operator rollback path. Returns
    the number of rows mutated."""
    if not pack_ids:
        return 0
    res = await session.execute(
        update(TopicPack).where(TopicPack.id.in_(pack_ids)).values(status=status)
    )
    return int(res.rowcount or 0)
