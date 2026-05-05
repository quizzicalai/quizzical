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

from app.models.db import (
    BaselineQuestionSet,
    Character,
    CharacterSet,
    Question,
    Synopsis,
    TopicPack,
)

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
    baseline_questions: tuple[dict[str, Any], ...] = ()
    """Each entry: ``{question_text, options:[{text, image_url?}, ...]}``.
    Empty tuple when the pack predates v3 / has no pre-baked questions —
    callers should fall back to the agent path for question generation in
    that case."""


def _coerce_uuid_list(raw_ids: Any) -> list[uuid.UUID]:
    """Best-effort UUID coercion for composition id lists."""
    out: list[uuid.UUID] = []
    for raw in list(raw_ids or []):
        try:
            out.append(uuid.UUID(str(raw)))
        except (TypeError, ValueError):
            continue
    return out


def _coerce_character_ids(raw_ids: Any) -> list[uuid.UUID]:
    """Best-effort UUID coercion for ``character_set.composition.character_ids``."""
    return _coerce_uuid_list(raw_ids)


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

    baseline_questions = await _resolve_baseline_questions(
        db, pack.baseline_question_set_id
    )

    return HydratedPack(
        pack_id=pack.id,
        topic_id=pack.topic_id,
        synopsis=_clean_synopsis(syn.body),
        characters=tuple(characters),
        baseline_questions=tuple(baseline_questions),
    )


def _clean_synopsis(body: dict[str, Any]) -> dict[str, Any]:
    """Project the persisted synopsis body to the schema the in-memory
    GraphState expects.

    The persisted ``synopses.body`` JSON may carry author-only metadata such
    as ``tone`` / ``themes`` (kept for editorial review). The runtime
    ``Synopsis`` pydantic model is ``StrictBase`` and only accepts
    ``title`` + ``summary``; passing extras through silently fails the Redis
    state save. Strip them here.
    """
    return {
        "title": str(body.get("title", "") or ""),
        "summary": str(body.get("summary", "") or ""),
    }


async def _resolve_baseline_questions(
    db: AsyncSession, bqs_id: uuid.UUID | None
) -> list[dict[str, Any]]:
    """Load pre-baked baseline questions for a pack's BaselineQuestionSet.

    Returns ``[]`` (caller falls back to the live agent path for question
    generation) when the BQS has no ``question_ids``, when no Question rows
    are found, or when the BQS row itself is missing.
    """
    if bqs_id is None:
        return []
    bqs = (
        await db.execute(select(BaselineQuestionSet).where(BaselineQuestionSet.id == bqs_id))
    ).scalar_one_or_none()
    if bqs is None or not isinstance(bqs.composition, dict):
        return []
    q_ids = _coerce_uuid_list(bqs.composition.get("question_ids"))
    if not q_ids:
        return []
    rows = (
        await db.execute(select(Question).where(Question.id.in_(q_ids)))
    ).scalars().all()
    by_id = {q.id: q for q in rows}
    out: list[dict[str, Any]] = []
    # AC-PROD-R6-PRECOMP-PHRASE-1 — keep the precomputed-pack flow visually
    # identical to the live agent path by injecting the same deterministic
    # baseline progress phrases the agent uses (see
    # `app.agent.tools.content_creation_tools.generate_baseline_questions`).
    from app.agent.progress_phrases import baseline_phrase_for_index

    for qid in q_ids:
        q = by_id.get(qid)
        if q is None:
            continue
        # Question.options is JSONB stored as ``{"items": [...]}`` by the v3
        # importer (matches the QuizQuestion schema's option list shape).
        opts_raw = q.options if isinstance(q.options, dict) else {}
        items = opts_raw.get("items")
        if not isinstance(items, list) or not items:
            continue
        out.append(
            {
                "question_text": q.text,
                "options": list(items),
                "progress_phrase": baseline_phrase_for_index(len(out)),
            }
        )
    return out


__all__ = ["HydratedPack", "hydrate_pack"]
