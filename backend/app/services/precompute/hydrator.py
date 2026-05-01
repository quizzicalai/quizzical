"""§21 Phase 3 — short-circuit pack hydrator.

Loads a published ``TopicPack`` and returns the synopsis + character profile
text needed by ``/quiz/start`` so the agent doesn't have to be invoked. The
caller is responsible for persisting the snapshot, scheduling FAL image
jobs, and building the API response — this module is intentionally read-only
and side-effect-free.

Acceptance criteria:
- AC-PRECOMP-HIT-1: A pack whose CharacterSet composition contains
  ``character_ids`` with at least one valid Character row hydrates fully.
- AC-PRECOMP-HIT-2: A pack whose CharacterSet composition is empty or whose
  referenced Character rows are missing returns ``None`` so the caller falls
  through to the live agent path.
- AC-PRECOMP-HIT-3: Synopsis body is read straight from ``synopses.body``;
  no LLM call is made.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db import Character, CharacterSet, Synopsis, TopicPack

logger = structlog.get_logger("app.services.precompute.hydrator")


@dataclass(frozen=True)
class HydratedPack:
    """Read-only view of a published pack's user-visible content."""

    pack_id: uuid.UUID
    topic_id: uuid.UUID
    synopsis: dict[str, Any]
    """``{title, summary, ...}`` — passed straight to the synopsis payload."""
    characters: tuple[dict[str, Any], ...]
    """Each entry: ``{name, short_description, profile_text, image_url}``."""


def _coerce_character_ids(raw_ids: Any) -> list[uuid.UUID]:
    """Best-effort UUID coercion for ``character_set.composition.character_ids``."""
    out: list[uuid.UUID] = []
    for raw in list(raw_ids or []):
        try:
            out.append(uuid.UUID(str(raw)))
        except (TypeError, ValueError):
            continue
    return out


async def _resolve_characters(
    db: AsyncSession, char_ids: list[uuid.UUID]
) -> list[dict[str, Any]]:
    rows = (
        await db.execute(select(Character).where(Character.id.in_(char_ids)))
    ).scalars().all()
    by_id = {c.id: c for c in rows}
    out: list[dict[str, Any]] = []
    for cid in char_ids:
        c = by_id.get(cid)
        if c is None:
            continue
        out.append(
            {
                "name": c.name,
                "short_description": c.short_description,
                "profile_text": c.profile_text,
                # Pre-baked packs ship text only; image URL is filled by the
                # FAL background pipeline scheduled from /quiz/start.
                "image_url": c.image_url,
            }
        )
    return out


async def hydrate_pack(
    db: AsyncSession, *, pack_id: uuid.UUID | str
) -> HydratedPack | None:
    """Return a ``HydratedPack`` for a published pack, or ``None`` if the
    pack lacks the content needed for a /quiz/start short-circuit.

    Returns ``None`` (caller falls through to agent) when:
      - the pack row doesn't exist or isn't ``published``;
      - the synopsis row is missing;
      - the character set composition has no ``character_ids``;
      - none of the referenced character rows exist.
    """
    # Coerce string ids (resolver returns ``str``) into ``uuid.UUID`` for the
    # native ``UUID`` column comparison; SQLAlchemy's bind processor calls
    # ``.hex`` directly on the value otherwise.
    if isinstance(pack_id, str):
        try:
            pack_id = uuid.UUID(pack_id)
        except ValueError:
            return None

    pack = (
        await db.execute(select(TopicPack).where(TopicPack.id == pack_id))
    ).scalar_one_or_none()
    if pack is None or pack.status != "published":
        return None

    syn = (
        await db.execute(select(Synopsis).where(Synopsis.id == pack.synopsis_id))
    ).scalar_one_or_none()
    if syn is None or not isinstance(syn.body, dict):
        logger.info(
            "precompute.hydrator.synopsis_missing",
            pack_id=str(pack_id),
            synopsis_id=str(pack.synopsis_id) if pack.synopsis_id else None,
        )
        return None

    char_set = (
        await db.execute(
            select(CharacterSet).where(CharacterSet.id == pack.character_set_id)
        )
    ).scalar_one_or_none()
    if char_set is None or not isinstance(char_set.composition, dict):
        return None

    char_ids = _coerce_character_ids(char_set.composition.get("character_ids"))
    if not char_ids:
        return None

    characters = await _resolve_characters(db, char_ids)
    if not characters:
        return None

    return HydratedPack(
        pack_id=pack.id,
        topic_id=pack.topic_id,
        synopsis=dict(syn.body),
        characters=tuple(characters),
    )


__all__ = ["HydratedPack", "hydrate_pack"]
