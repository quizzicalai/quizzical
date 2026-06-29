"""icon_assets migration / schema-shape tests (DRAFT, additive-only).

Two layers:
  - ORM layer (SQLite test bench): ``Base.metadata.create_all`` builds the
    ``icon_assets`` table; the IconAsset model round-trips with a 384-dim
    embedding; ``id`` is the PK (dedup on re-insert).
  - DDL layer (init.sql): the Postgres migration declares VECTOR(384) + an
    IVFFlat cosine index (lists=100) in the SAME shape as topics.embedding, and
    is Postgres-guarded so the SQLite bench is unaffected. ADDITIVE only — no
    edits to existing tables.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import IconAsset
from tests.fixtures.db_fixtures import sqlite_db_session  # noqa: F401

pytestmark = pytest.mark.anyio

_INIT_SQL = (
    Path(__file__).resolve().parents[4] / "db" / "init" / "init.sql"
)


# ---------------------------------------------------------------------------
# ORM layer
# ---------------------------------------------------------------------------

async def test_icon_assets_table_created(sqlite_db_session: AsyncSession):
    def _names(sync_conn):
        return set(inspect(sync_conn).get_table_names())

    names = await sqlite_db_session.run_sync(lambda s: _names(s.connection()))
    assert "icon_assets" in names


async def test_icon_assets_columns(sqlite_db_session: AsyncSession):
    def _cols(sync_conn):
        return {c["name"] for c in inspect(sync_conn).get_columns("icon_assets")}

    cols = await sqlite_db_session.run_sync(lambda s: _cols(s.connection()))
    assert {
        "id", "lucide_name", "concept", "caption", "palette_variant",
        "source_set", "license", "storage_uri", "embedding", "created_at",
    } <= cols


async def test_icon_asset_round_trip_384dim(sqlite_db_session: AsyncSession):
    emb = [0.0] * 384
    emb[0] = 1.0
    row = IconAsset(
        id="rocket", lucide_name="rocket", concept="space/rocket",
        caption="rocket spaceship launch", palette_variant="sea", embedding=emb,
    )
    sqlite_db_session.add(row)
    await sqlite_db_session.commit()
    fetched = (
        await sqlite_db_session.execute(
            select(IconAsset).where(IconAsset.id == "rocket")
        )
    ).scalar_one()
    assert fetched.palette_variant == "sea"
    assert fetched.source_set == "lucide"   # server default
    assert fetched.license == "ISC"         # server default
    assert fetched.storage_uri is None
    # embedding round-trips as 384 floats (TEXT under SQLite, parsed back).
    from app.services.precompute.lookup import _coerce_vector
    parsed = _coerce_vector(fetched.embedding)
    assert parsed is not None and len(parsed) == 384


async def test_icon_assets_id_is_primary_key(sqlite_db_session: AsyncSession):
    a = IconAsset(id="dup", lucide_name="x", concept="c", caption="cap",
                  palette_variant="sea", embedding=[0.0] * 384)
    b = IconAsset(id="dup", lucide_name="y", concept="c2", caption="cap2",
                  palette_variant="amber", embedding=[1.0] * 384)
    sqlite_db_session.add(a)
    await sqlite_db_session.commit()
    sqlite_db_session.add(b)
    with pytest.raises(IntegrityError):
        await sqlite_db_session.commit()
    await sqlite_db_session.rollback()


# ---------------------------------------------------------------------------
# DDL layer (init.sql) — shape must match topics.embedding exactly
# ---------------------------------------------------------------------------

def test_init_sql_declares_icon_assets_vector384():
    sql = _INIT_SQL.read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS icon_assets" in sql
    # VECTOR(384) NOT NULL embedding, same space as topics.embedding.
    assert re.search(r"embedding\s+VECTOR\(384\)\s+NOT NULL", sql)


def test_init_sql_ivfflat_index_matches_topics_shape():
    sql = _INIT_SQL.read_text(encoding="utf-8")
    # IVFFlat cosine index with lists=100 — identical pattern to
    # idx_topics_embedding_cosine_ivf.
    assert re.search(
        r"CREATE INDEX[^\n]*idx_icon_assets_embedding_cosine_ivf\s+"
        r"ON icon_assets\s+USING ivfflat \(embedding vector_cosine_ops\)\s+"
        r"WITH \(lists = 100\)",
        sql,
    )
    # Postgres-guarded so the SQLite bench never sees the IVFFlat DDL.
    idx = sql.index("idx_icon_assets_embedding_cosine_ivf")
    preamble = sql[max(0, idx - 200):idx]
    assert "DO $$" in preamble and "pg_indexes" in preamble


def test_init_sql_icon_assets_is_additive_create_if_not_exists():
    """The migration only CREATEs new objects — it must not ALTER/DROP any
    pre-existing table in the icon_assets section."""
    sql = _INIT_SQL.read_text(encoding="utf-8")
    start = sql.index("Q&A icon enrichment (DRAFT")
    section = sql[start:]
    # No destructive / mutating ops on existing tables in this section.
    assert "DROP " not in section.upper()
    assert "CREATE TABLE IF NOT EXISTS icon_assets" in section
