"""Seed tests — icon_assets is seeded idempotently from the bundled index.

No model load / no FAL: the seed embeddings are precomputed and shipped in
``app/services/icons/data/icon_index.json``.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import IconAsset
from app.services.icons.index import (
    load_icon_index_from_db,
    load_icon_index_from_file,
    seed_path,
)
from app.services.icons.seed import seed_icon_assets
from tests.fixtures.db_fixtures import sqlite_db_session  # noqa: F401

pytestmark = pytest.mark.anyio


def test_seed_file_is_119_icons_384dim():
    data = json.loads(seed_path().read_text(encoding="utf-8"))
    assert data["dim"] == 384
    assert data["model"] == "BAAI/bge-small-en-v1.5"
    assert data["n_icons"] == 119
    assert len(data["icons"]) == 119
    assert all(len(ic["embedding"]) == 384 for ic in data["icons"])


def test_load_icon_index_from_file_parses_all():
    idx = load_icon_index_from_file()
    assert len(idx) == 119
    first = idx[0]
    assert first.id and len(first.embedding) == 384
    assert first.palette_variant in {"sea", "indigo", "amber", "slate"}


async def test_seed_inserts_all_rows(sqlite_db_session: AsyncSession):
    n = await seed_icon_assets(sqlite_db_session)
    assert n == 119
    count = (
        await sqlite_db_session.execute(select(func.count()).select_from(IconAsset))
    ).scalar_one()
    assert count == 119


async def test_seed_is_idempotent(sqlite_db_session: AsyncSession):
    n1 = await seed_icon_assets(sqlite_db_session)
    await sqlite_db_session.commit()
    n2 = await seed_icon_assets(sqlite_db_session)  # second run inserts nothing new
    assert n1 == 119
    assert n2 == 0
    count = (
        await sqlite_db_session.execute(select(func.count()).select_from(IconAsset))
    ).scalar_one()
    assert count == 119


async def test_seeded_rows_loadable_by_binder_index(sqlite_db_session: AsyncSession):
    await seed_icon_assets(sqlite_db_session)
    idx = await load_icon_index_from_db(sqlite_db_session)
    assert len(idx) == 119
    # Each candidate exposes a parseable 384-dim embedding (ready for cosine).
    assert all(len(c.embedding) == 384 for c in idx)
    ids = {c.id for c in idx}
    assert "rocket" in ids  # a known catalog id round-tripped through the DB
