"""§21 Phase 4 — `mv_topic_pack_resolved` refresh helper tests.

`AC-PRECOMP-PERF-1` — publish() refreshes the MV concurrently. The actual
MV only exists in Postgres; on the SQLite test bench the helper short-
circuits (returns False) so callers can still invoke it unconditionally.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.precompute import mv_refresh
from app.services.precompute.mv_refresh import (
    MV_NAME,
    REFRESH_SQL,
    refresh_mv_topic_pack_resolved,
)

pytestmark = pytest.mark.anyio


def _make_session(dialect_name: str) -> Any:
    """Minimal AsyncSession-shaped stub with a configurable dialect name."""
    session = MagicMock()
    bind = MagicMock()
    bind.dialect = MagicMock()
    bind.dialect.name = dialect_name
    session.get_bind.return_value = bind
    session.execute = AsyncMock()
    return session


async def test_refresh_runs_on_postgres():
    s = _make_session("postgresql")
    ran = await refresh_mv_topic_pack_resolved(s)
    assert ran is True
    s.execute.assert_awaited_once()
    args, _ = s.execute.call_args
    assert REFRESH_SQL in str(args[0])
    assert "CONCURRENTLY" in REFRESH_SQL


async def test_refresh_skips_on_sqlite():
    s = _make_session("sqlite")
    ran = await refresh_mv_topic_pack_resolved(s)
    assert ran is False
    s.execute.assert_not_called()


async def test_refresh_failure_returns_false_does_not_raise():
    s = _make_session("postgresql")
    s.execute.side_effect = RuntimeError("locked")
    ran = await refresh_mv_topic_pack_resolved(s)
    assert ran is False  # logged + swallowed


def test_mv_name_constant_matches_init_sql():
    """Sanity: keep the helper and the schema in sync."""
    assert MV_NAME == "mv_topic_pack_resolved"
    assert "CONCURRENTLY" in REFRESH_SQL
    assert MV_NAME in REFRESH_SQL


def test_module_exports_helper():
    assert hasattr(mv_refresh, "refresh_mv_topic_pack_resolved")
