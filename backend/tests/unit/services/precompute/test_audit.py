"""§21 Phase 3 — append-only audit log tests (`AC-PRECOMP-FLAG-6`)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.db import AuditLog
from app.services.precompute.audit import record_operator_action
from tests.fixtures.db_fixtures import sqlite_db_session  # noqa: F401

pytestmark = pytest.mark.anyio


async def test_record_operator_action_persists_with_hashes(sqlite_db_session) -> None:
    row = await record_operator_action(
        sqlite_db_session,
        actor_id="operator:bearer", action="precompute.promote",
        target_kind="topic", target_id="t-1",
        before={"current_pack_id": None},
        after={"current_pack_id": "p-9"},
        extra={"note": "manual promote"},
    )
    await sqlite_db_session.commit()

    found = (
        await sqlite_db_session.execute(select(AuditLog).where(AuditLog.id == row.id))
    ).scalar_one()
    assert found.actor_id == "operator:bearer"
    assert found.action == "precompute.promote"
    assert found.target_kind == "topic"
    assert found.target_id == "t-1"
    assert found.before_hash and found.after_hash
    assert found.before_hash != found.after_hash
    # Hashes are deterministic SHA-256 hex strings.
    assert len(found.before_hash) == 64


async def test_record_rejects_missing_required_fields(sqlite_db_session) -> None:
    with pytest.raises(ValueError):
        await record_operator_action(
            sqlite_db_session,
            actor_id="", action="x", target_kind="y", target_id="z",
        )
