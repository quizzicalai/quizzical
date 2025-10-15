"""
Database Service (Repository Pattern) — aligned with the new schema.

Implements:
- CharacterRepository
- SessionRepository   (persists agent_plan & character_set; fixed bulk link insert)
- SessionQuestionsRepository
- ResultService

All methods use AsyncSession and PostgreSQL upserts (ON CONFLICT).
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

import structlog
from fastapi import Depends
from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.api.dependencies import get_db_session
from app.models.api import FeedbackRatingEnum, ShareableResultResponse
from app.models.db import (
    Character,
    SessionHistory,
    SessionQuestions,
    UserSentimentEnum,
    character_session_map,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _omit_none(d: Dict[str, Any]) -> Dict[str, Any]:
    """Return a shallow copy without keys whose value is None."""
    return {k: v for k, v in (d or {}).items() if v is not None}


# =============================================================================
# CharacterRepository
# =============================================================================

class CharacterRepository:
    """DB operations for Character."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_id(self, character_id: uuid.UUID) -> Character | None:
        return await self.session.get(Character, character_id)

    async def get_many_by_ids(self, character_ids: List[uuid.UUID]) -> List[Character]:
        if not character_ids:
            return []
        result = await self.session.execute(
            select(Character).where(Character.id.in_(character_ids))
        )
        return list(result.scalars().all())

    async def create(self, name: str, **kwargs) -> Character:
        obj = Character(name=name, **kwargs)
        self.session.add(obj)
        await self.session.flush()
        await self.session.refresh(obj)
        return obj

    async def upsert_by_name(
        self, *, name: str, short_description: str = "", profile_text: str = ""
    ) -> Character:
        """
        Upsert Character by unique 'name'. Returns the ORM object.
        """
        stmt = (
            pg_insert(Character)
            .values(name=name, short_description=short_description, profile_text=profile_text)
            .on_conflict_do_update(
                index_elements=[Character.__table__.c.name],
                set_={
                    "short_description": short_description,
                    "profile_text": profile_text,
                    "last_updated_at": func.now(),
                },
            )
            .returning(Character)
        )
        result = await self.session.execute(stmt)
        row = result.fetchone()
        if row is None:
            # Rare path: fetch explicitly
            resel = await self.session.execute(select(Character).where(Character.name == name))
            return resel.scalars().first()
        return row[0]

    async def update_profile(self, character_id: uuid.UUID, new_profile_text: str) -> Character | None:
        obj = await self.session.get(Character, character_id)
        if not obj:
            return None
        obj.profile_text = new_profile_text
        obj.judge_quality_score = None
        await self.session.flush()
        await self.session.refresh(obj)
        return obj

    async def set_profile_picture(self, character_id: uuid.UUID, image_bytes: bytes) -> bool:
        obj = await self.session.get(Character, character_id)
        if not obj:
            return False
        obj.profile_picture = image_bytes
        await self.session.flush()
        return True


# =============================================================================
# SessionRepository
# =============================================================================

class SessionRepository:
    """DB operations for SessionHistory and character linkage."""

    def __init__(self, session: AsyncSession):
        self.session = session

    # --- Reads ---

    async def get_by_id(self, session_id: uuid.UUID) -> Optional[SessionHistory]:
        return await self.session.get(SessionHistory, session_id)

    # --- Writes / Upserts ---

    async def upsert_session_after_synopsis(
        self,
        *,
        session_id: uuid.UUID,
        category: str,
        synopsis_dict: Dict[str, Any],
        transcript: List[Dict[str, Any]] | List[Any],
        characters_payload: Optional[List[Dict[str, Any]]] = None,
        completed: bool = False,
        agent_plan: Optional[Dict[str, Any]] = None,
        character_set: Optional[List[Dict[str, Any]]] = None,
    ) -> SessionHistory:
        """
        Upsert the session row with synopsis & transcript, optionally persisting
        agent_plan and the character_set snapshot, then upsert/link characters.

        Notes:
        - `character_set` is NOT NULL in the DB with a server default of '[]'.
          We only include it in INSERT/UPDATE when a non-None payload is given,
          so the DB default applies otherwise.
        """
        # 1) Upsert session
        insert_values = {
            "session_id": session_id,
            "category": category,
            "category_synopsis": synopsis_dict,
            "session_transcript": list(transcript or []),
            "final_result": None,
            "is_completed": completed,
            # Nullable in DB; include only if provided
            **_omit_none({"agent_plan": agent_plan}),
            # NOT NULL with default; include only if provided
            **(_omit_none({"character_set": character_set})),
        }

        update_values = {
            "category": category,
            "category_synopsis": synopsis_dict,
            "session_transcript": list(transcript or []),
            "last_updated_at": func.now(),
            # Only set when provided (avoid writing NULLs)
            **_omit_none({"agent_plan": agent_plan}),
            **_omit_none({"character_set": character_set}),
        }

        stmt = (
            pg_insert(SessionHistory)
            .values(insert_values)
            .on_conflict_do_update(
                index_elements=[SessionHistory.__table__.c.session_id],
                set_=update_values,
            )
            .returning(SessionHistory)
        )
        res = await self.session.execute(stmt)
        sess_row = res.fetchone()
        session_obj: Optional[SessionHistory]
        if sess_row is None:
            session_obj = await self.session.get(SessionHistory, session_id)
        else:
            session_obj = sess_row[0]

        # 2) Upsert characters and link
        if characters_payload:
            ids: List[uuid.UUID] = []
            for c in characters_payload:
                name = (c or {}).get("name", "")
                if not name:
                    continue
                short_description = (c or {}).get("short_description", "") or ""
                profile_text = (c or {}).get("profile_text", "") or ""
                char = await CharacterRepository(self.session).upsert_by_name(
                    name=name, short_description=short_description, profile_text=profile_text
                )
                if char:
                    ids.append(char.id)

            if ids:
                # Insert links; ignore duplicates
                link_stmt = (
                    pg_insert(character_session_map)
                    .values([{"character_id": cid, "session_id": session_id} for cid in ids])
                    .on_conflict_do_nothing()
                )
                await self.session.execute(link_stmt)

        await self.session.flush()
        if session_obj:
            await self.session.refresh(session_obj)
        return session_obj  # type: ignore[return-value]

    async def mark_completed(
        self,
        *,
        session_id: uuid.UUID,
        final_result: Optional[Dict[str, Any]],
        qa_history: Optional[List[Dict[str, Any]]] = None,
    ) -> bool:
        """
        Set final_result, qa_history, is_completed = TRUE, completed_at = now().
        """
        stmt = (
            update(SessionHistory)
            .where(SessionHistory.session_id == session_id)
            .values(
                final_result=final_result,
                qa_history=list(qa_history or []),
                is_completed=True,
                completed_at=func.now(),
                last_updated_at=func.now(),
            )
        )
        res = await self.session.execute(stmt)
        return (res.rowcount or 0) > 0

    async def save_feedback(
        self,
        session_id: uuid.UUID,
        rating: FeedbackRatingEnum,
        feedback_text: Optional[str],
    ) -> Optional[SessionHistory]:
        """
        Map 'up'/'down' to POSITIVE/NEGATIVE and store optional text.
        """
        rating_str = getattr(rating, "value", str(rating)).lower()
        sentiment = {
            "up": UserSentimentEnum.POSITIVE,
            "down": UserSentimentEnum.NEGATIVE,
        }.get(rating_str, UserSentimentEnum.NONE)

        stmt = (
            update(SessionHistory)
            .where(SessionHistory.session_id == session_id)
            .values(
                user_sentiment=sentiment,
                user_feedback_text=feedback_text,
                last_updated_at=func.now(),
            )
        )
        res = await self.session.execute(stmt)
        if (res.rowcount or 0) == 0:
            return None
        return await self.session.get(SessionHistory, session_id)


# =============================================================================
# SessionQuestionsRepository
# =============================================================================

class SessionQuestionsRepository:
    """DB operations for SessionQuestions (1 row per session)."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_for_session(self, session_id: uuid.UUID) -> Optional[SessionQuestions]:
        return await self.session.get(SessionQuestions, session_id)

    async def baseline_exists(self, session_id: uuid.UUID) -> bool:
        result = await self.session.execute(
            select(SessionQuestions.session_id).where(
                (SessionQuestions.session_id == session_id)
                & (SessionQuestions.baseline_questions.is_not(None))
            )
        )
        return result.first() is not None

    async def upsert_baseline(
        self,
        *,
        session_id: uuid.UUID,
        baseline_blob: Dict[str, Any],
        properties: Optional[Dict[str, Any]] = None,
    ) -> SessionQuestions:
        stmt = (
            pg_insert(SessionQuestions)
            .values(
                session_id=session_id,
                baseline_questions=baseline_blob,
                properties=properties or {},
            )
            .on_conflict_do_update(
                index_elements=[SessionQuestions.__table__.c.session_id],
                set_={
                    "baseline_questions": baseline_blob,
                    "last_updated_at": func.now(),
                },
            )
            .returning(SessionQuestions)
        )
        res = await self.session.execute(stmt)
        row = res.fetchone()
        return row[0] if row else await self.session.get(SessionQuestions, session_id)

    async def upsert_adaptive(
        self,
        *,
        session_id: uuid.UUID,
        adaptive_blob: Dict[str, Any],
        properties: Optional[Dict[str, Any]] = None,
    ) -> SessionQuestions:
        stmt = (
            pg_insert(SessionQuestions)
            .values(
                session_id=session_id,
                adaptive_questions=adaptive_blob,
                properties=properties or {},
            )
            .on_conflict_do_update(
                index_elements=[SessionQuestions.__table__.c.session_id],
                set_={
                    "adaptive_questions": adaptive_blob,
                    "last_updated_at": func.now(),
                },
            )
            .returning(SessionQuestions)
        )
        res = await self.session.execute(stmt)
        row = res.fetchone()
        return row[0] if row else await self.session.get(SessionQuestions, session_id)


# =============================================================================
# ResultService
# =============================================================================

class ResultService:
    """
    Retrieve a shareable result. Returns None if not found or not completed.
    """

    def __init__(self, session: AsyncSession = Depends(get_db_session)):
        self.session = session

    async def get_result_by_id(self, result_id: uuid.UUID) -> Optional[ShareableResultResponse]:
        record = await self.session.get(SessionHistory, result_id)
        if not record or not record.final_result:
            return None

        normalized = normalize_final_result(record.final_result)
        if not normalized:
            return None

        return ShareableResultResponse(
            title=normalized.get("title", ""),
            description=normalized.get("description", ""),
            image_url=normalized.get("image_url"),
            category=record.category,
            created_at=str(record.created_at) if getattr(record, "created_at", None) else None,
        )


# =============================================================================
# Helpers
# =============================================================================

def normalize_final_result(raw_result: Any) -> Optional[Dict[str, str]]:
    """
    Normalize various formats of final_result into a consistent dict:
    {title, description, image_url}
    """
    if not raw_result:
        return None

    # Pydantic v2 object
    if hasattr(raw_result, "model_dump"):
        try:
            raw_result = raw_result.model_dump()
        except Exception:
            pass
    # Pydantic v1 / dataclass-like
    elif hasattr(raw_result, "dict"):
        try:
            raw_result = raw_result.dict()
        except Exception:
            pass

    if isinstance(raw_result, str):
        return {"title": "Quiz Result", "description": raw_result, "image_url": ""}

    if isinstance(raw_result, dict):
        title = raw_result.get("title") or raw_result.get("profileTitle") or "Quiz Result"
        description = raw_result.get("description") or raw_result.get("summary") or ""
        image_url = raw_result.get("image_url") or raw_result.get("imageUrl") or ""
        return {"title": title, "description": description, "image_url": image_url}

    logger.warning("normalize_final_result: unsupported type", type=type(raw_result).__name__)
    return None
