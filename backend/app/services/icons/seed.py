"""Seed the ``icon_assets`` table from the bundled icon index (DRAFT).

The recolored brand-icon library + rich captions + 384-dim embeddings come from
the validated prototype (``prototype/qa-image-enrichment``: ``data/icon_index.json``,
119 icons, BAAI/bge-small-en-v1.5). The embeddings are precomputed at build time
(embed once, ever) and shipped in ``app/services/icons/data/icon_index.json`` so
seeding needs NO model load and NO FAL spend.

Idempotent: existing rows (by ``id``) are left untouched (ON CONFLICT DO NOTHING
semantics), so this is safe to re-run.

Usage (operator / one-off, requires DATABASE_URL or DATABASE_* env):

    cd backend && APP_ENVIRONMENT=local .venv312/Scripts/python.exe -m app.services.icons.seed

Programmatic (e.g. an admin endpoint / test):

    from app.services.icons.seed import seed_icon_assets
    n = await seed_icon_assets(session)
"""

from __future__ import annotations

import asyncio
import json

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import IconAsset
from app.services.icons.index import seed_path

logger = structlog.get_logger(__name__)


def _load_seed_rows() -> list[dict]:
    data = json.loads(seed_path().read_text(encoding="utf-8"))
    rows: list[dict] = []
    for ic in data.get("icons", []):
        emb = ic.get("embedding")
        if not emb or len(emb) != int(data.get("dim", 384)):
            continue
        rows.append(
            {
                "id": ic["id"],
                "lucide_name": ic.get("lucide", ic["id"]),
                "concept": ic.get("concept", ""),
                "caption": ic.get("caption", ""),
                "palette_variant": ic.get("palette_variant", "sea"),
                "embedding": [float(x) for x in emb],
            }
        )
    return rows


async def seed_icon_assets(session: AsyncSession, *, flush: bool = True) -> int:
    """Insert any icon rows that are not already present. Returns the number of
    NEW rows inserted (existing ids are skipped). Does NOT commit — the caller
    owns the transaction, matching the repo's session conventions."""
    rows = _load_seed_rows()
    if not rows:
        return 0

    existing = set(
        (await session.execute(select(IconAsset.id))).scalars().all()
    )
    inserted = 0
    for r in rows:
        if r["id"] in existing:
            continue
        session.add(
            IconAsset(
                id=r["id"],
                lucide_name=r["lucide_name"],
                concept=r["concept"],
                caption=r["caption"],
                palette_variant=r["palette_variant"],
                embedding=r["embedding"],
            )
        )
        inserted += 1
    if flush and inserted:
        await session.flush()
    logger.info("icons.seed.done", inserted=inserted, total=len(rows))
    return inserted


async def _amain() -> None:
    from app.api.dependencies import create_db_engine_and_session_maker
    from app.core.config import settings

    db_url = settings.DATABASE_URL
    if not db_url:
        raise SystemExit("DATABASE_URL not configured")
    _, session_factory = create_db_engine_and_session_maker(db_url)
    assert session_factory is not None
    async with session_factory() as session:
        n = await seed_icon_assets(session)
        await session.commit()
    print(f"seeded {n} new icon_assets rows")


if __name__ == "__main__":
    asyncio.run(_amain())
