"""§21 Phase 3 — append-only operator audit (`AC-PRECOMP-FLAG-6`).

Every operator-facing mutation (`enqueue`, `promote`, `rollback`,
`forget`, flag-resolve …) MUST persist a row in `audit_log` capturing
**who** did **what** to **which target**, with content hashes that let an
auditor verify the before / after state without us storing the bodies.

This module exposes a single coroutine, `record_operator_action`, that
the admin endpoints and the Phase-6 flag resolver call. The module is
careful never to log the actor token or the secret-bearing `extra` dict.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import AuditLog
from app.services.precompute.dedup import content_hash

logger = structlog.get_logger("app.services.precompute.audit")


def _hash_or_none(payload: Any) -> str | None:
    """Hash a JSON-serialisable payload; pass-through `None`."""
    if payload is None:
        return None
    try:
        return content_hash(payload)
    except (TypeError, ValueError):
        return content_hash(json.dumps(payload, default=str))


async def record_operator_action(
    db: AsyncSession,
    *,
    actor_id: str,
    action: str,
    target_kind: str,
    target_id: str,
    before: Any | None = None,
    after: Any | None = None,
    extra: dict[str, Any] | None = None,
) -> AuditLog:
    """Insert an append-only audit row and flush; do NOT commit.

    The caller (a transactional endpoint) commits as part of its own
    transaction so the audit row and the underlying mutation either both
    persist or both roll back.
    """
    if not actor_id or not action or not target_kind or not target_id:
        raise ValueError("audit row requires actor_id, action, target_kind, target_id")

    row = AuditLog(
        actor_id=str(actor_id)[:128],
        action=str(action)[:64],
        target_kind=str(target_kind)[:32],
        target_id=str(target_id)[:128],
        before_hash=_hash_or_none(before),
        after_hash=_hash_or_none(after),
        extra=extra,
    )
    db.add(row)
    await db.flush()
    logger.info(
        "audit_log.recorded",
        actor_id=row.actor_id,
        action=row.action,
        target_kind=row.target_kind,
        target_id=row.target_id,
        before_hash=row.before_hash,
        after_hash=row.after_hash,
    )
    return row
