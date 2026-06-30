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
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

import structlog
from fastapi import Depends
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db_session
from app.models.api import FeedbackRatingEnum, ShareableResultResponse
from app.models.db import (
    Character,
    QuizJob,
    SessionHistory,
    SessionQuestions,
    UserSentimentEnum,
    character_session_map,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _omit_none(d: dict[str, Any]) -> dict[str, Any]:
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

    async def get_many_by_ids(self, character_ids: list[uuid.UUID]) -> list[Character]:
        # §15.6 — bound the IN-list to prevent unbounded queries (AC-IDS-1..3).
        MAX_IDS = 100
        if not character_ids:
            return []
        if len(character_ids) > MAX_IDS:
            raise ValueError(f"ids list exceeds maximum ({MAX_IDS})")
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

        Populate ``canonical_key`` (the deterministic, accent-folded,
        whitespace-collapsed dedup key, ``AC-PRECOMP-DEDUP-1``) on every upsert
        so runtime-created rows stay in lock-step with the ``init.sql`` backfill
        and the precompute dedup helpers (``find_character_by_canonical_key``).
        The key is derived from ``name`` and is therefore idempotent. NOTE: it is
        intentionally NOT used to scope image-URL reuse — it is non-unique and
        distinct names collide under it.
        """
        # Local import keeps this module importable without pulling the
        # precompute package at import time (mirrors the pipeline's usage).
        from app.services.precompute.canonicalize import canonical_key_for_name

        ckey = canonical_key_for_name(name) or None
        stmt = (
            pg_insert(Character)
            .values(
                name=name,
                short_description=short_description,
                profile_text=profile_text,
                canonical_key=ckey,
            )
            .on_conflict_do_update(
                index_elements=[Character.__table__.c.name],
                set_={
                    "short_description": short_description,
                    "profile_text": profile_text,
                    "canonical_key": ckey,
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

    async def get_by_id(self, session_id: uuid.UUID) -> SessionHistory | None:
        return await self.session.get(SessionHistory, session_id)

    # --- Writes / Upserts ---

    async def upsert_session_after_synopsis(
        self,
        *,
        session_id: uuid.UUID,
        category: str,
        synopsis_dict: dict[str, Any],
        transcript: list[dict[str, Any]] | list[Any],
        characters_payload: list[dict[str, Any]] | None = None,
        completed: bool = False,
        agent_plan: dict[str, Any] | None = None,
        character_set: list[dict[str, Any]] | None = None,
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
        session_obj: SessionHistory | None
        if sess_row is None:
            session_obj = await self.session.get(SessionHistory, session_id)
        else:
            session_obj = sess_row[0]

        # 2) Upsert characters and link
        if characters_payload:
            ids: list[uuid.UUID] = []
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
        final_result: dict[str, Any] | None,
        qa_history: list[dict[str, Any]] | None = None,
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

    async def update_qa_history(  # NEW: incremental durability of answers
        self,
        *,
        session_id: uuid.UUID,
        qa_history: list[dict[str, Any]],
    ) -> bool:
        """
        Persist the latest QA history without marking the session complete.
        Safe to call after each /quiz/next.
        """
        stmt = (
            update(SessionHistory)
            .where(SessionHistory.session_id == session_id)
            .values(
                qa_history=list(qa_history or []),
                last_updated_at=func.now(),
            )
        )
        res = await self.session.execute(stmt)
        return (res.rowcount or 0) > 0

    async def purge_older_than(self, *, days: int) -> int:
        """§17.3 (AC-SCALE-RETENTION-1..4) — delete sessions whose
        ``last_updated_at`` is older than ``days`` days ago.

        Cascading FKs on ``character_session_map`` clean up linkage rows.
        Returns the number of session rows deleted. Raises ``ValueError``
        when ``days < 1`` so a misconfigured cron cannot wipe the table.
        """
        if not isinstance(days, int) or days < 1:
            raise ValueError("days must be a positive integer (>=1)")

        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = (
            delete(SessionHistory)
            .where(SessionHistory.last_updated_at < cutoff)
        )
        res = await self.session.execute(stmt)
        return int(res.rowcount or 0)

    async def save_feedback(
        self,
        session_id: uuid.UUID,
        rating: FeedbackRatingEnum,
        feedback_text: str | None,
    ) -> SessionHistory | None:
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

    async def get_for_session(self, session_id: uuid.UUID) -> SessionQuestions | None:
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
        baseline_blob: dict[str, Any],
        properties: dict[str, Any] | None = None,
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
                    **_omit_none({"properties": properties}),  # update properties if provided
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
        adaptive_blob: dict[str, Any],
        properties: dict[str, Any] | None = None,
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
                    **_omit_none({"properties": properties}),  # update properties if provided
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

    def __init__(self, session: Annotated[AsyncSession, Depends(get_db_session)]):
        self.session = session

    async def get_result_by_id(self, result_id: uuid.UUID) -> ShareableResultResponse | None:
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
            # Blended-profile pilot: carry the blend through so a shared /
            # reopened DISC link renders BlendedProfileResult instead of
            # downgrading to single-character. Single-character results leave
            # these None (and the serializer omits them).
            result_kind=normalized.get("result_kind"),
            profile=normalized.get("profile"),
        )


# =============================================================================
# Helpers
# =============================================================================

def normalize_final_result(raw_result: Any) -> dict[str, Any] | None:
    """
    Normalize various formats of final_result into a consistent dict:
    {title, description, image_url, result_kind?, profile?}

    The optional ``result_kind``/``profile`` are preserved for the
    blended-profile pilot so a shared / reopened DISC result still renders the
    blend. They are read under either snake_case or camelCase (the stored dict
    can be either, since it is the JSON-encoded final_result). Single-character
    results carry neither (left as ``None``).
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
        result_kind = raw_result.get("result_kind") or raw_result.get("resultKind")
        profile = raw_result.get("profile")
        return {
            "title": title,
            "description": description,
            "image_url": image_url,
            "result_kind": result_kind,
            "profile": profile,
        }

    logger.warning("normalize_final_result: unsupported type", type=type(raw_result).__name__)
    return None


# ---------------------------------------------------------------------------
# Durable live-agent job tracking + crash-recovery (quiz_jobs)
# ---------------------------------------------------------------------------
class QuizJobRepository:
    """Tracks in-flight live agent runs so a worker death can't strand a quiz.

    Portable (no PG-only ``ON CONFLICT``) so the sqlite test DB exercises the
    real logic. The recovery sweeper relies on ``claim_stale`` being an atomic
    UPDATE so concurrent replicas never re-run the same job twice.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def mark_running(
        self, quiz_id: uuid.UUID, *, phase: str = "agent", reset_attempts: bool = False
    ) -> None:
        """Mark a job running (creating the row if absent).

        ``attempts`` is the RECOVERY counter that gates ``claim_stale`` /
        ``fail_exhausted``: each (re-)invocation of ``run_agent_in_background``
        bumps it, so the sweeper gives up after ``max_attempts`` re-runs.

        DOUBLE-BUMP CAVEAT (review item D): for a RECOVERY re-run, ``claim_stale``
        already incremented ``attempts`` (+1) before this ``mark_running`` (+1),
        so a recovery that reaches here consumes 2 of the budget per re-run —
        i.e. the effective recovery budget is ~``max_attempts / 2`` full re-runs.
        See ``claim_stale`` and the ``security.agent_recovery.max_attempts``
        config comment.

        ``reset_attempts=True`` is used when the request handler creates the row
        synchronously for a NEW user-initiated run (before scheduling the bg
        task): it sets ``attempts=0`` so the bg task's own ``mark_running`` makes
        this run attempt 1, and the per-run recovery budget is independent of how
        many prior quiz steps (/proceed, /next) already touched the row. Without
        this, a multi-question quiz would accumulate attempts across steps and
        prematurely exhaust the recovery budget for a later crash.
        """
        job = await self.session.get(QuizJob, quiz_id)
        now = datetime.now(timezone.utc)
        if job is None:
            self.session.add(
                QuizJob(
                    quiz_id=quiz_id, phase=phase, status="running",
                    attempts=0 if reset_attempts else 1,
                    last_heartbeat_at=now, last_error=None,
                )
            )
        else:
            job.status = "running"
            job.attempts = 0 if reset_attempts else int(job.attempts or 0) + 1
            job.last_heartbeat_at = now
            job.phase = phase
            job.last_error = None

    async def heartbeat(self, quiz_id: uuid.UUID) -> None:
        await self.session.execute(
            update(QuizJob)
            .where(QuizJob.quiz_id == quiz_id, QuizJob.status == "running")
            .values(last_heartbeat_at=datetime.now(timezone.utc))
        )

    async def mark_succeeded(self, quiz_id: uuid.UUID) -> None:
        await self.session.execute(
            update(QuizJob)
            .where(QuizJob.quiz_id == quiz_id)
            .values(status="succeeded", last_error=None, last_heartbeat_at=datetime.now(timezone.utc))
        )

    async def mark_failed(self, quiz_id: uuid.UUID, error: str | None = None) -> None:
        await self.session.execute(
            update(QuizJob)
            .where(QuizJob.quiz_id == quiz_id)
            .values(status="failed", last_error=(error or "")[:2000], last_heartbeat_at=datetime.now(timezone.utc))
        )

    async def mark_retryable(self, quiz_id: uuid.UUID, error: str | None = None) -> None:
        """Hitlist #8 (2026-06-30) — mark an in-process run that failed
        TRANSIENTLY as recoverable rather than terminally failed.

        The recovery sweeper's ``claim_stale`` only claims ``status=='running'``
        rows whose heartbeat is older than ``stale_after_s``. So to hand a
        transient failure back to the sweeper we keep status ``running`` but set
        ``last_heartbeat_at`` to the epoch (older than ANY staleness deadline) —
        the very next sweep re-claims it immediately, bumping ``attempts`` so the
        re-spend is still bounded by ``max_attempts`` (and ``fail_exhausted``
        eventually fails it). We do NOT touch ``attempts`` here: this run already
        consumed one (via ``mark_running``); the next sweep's ``claim_stale``
        adds the next. The error is recorded for observability.

        Status stays ``running`` (not a new value) on purpose so the existing
        ``claim_stale`` / ``fail_exhausted`` / ``get_status`` machinery needs no
        changes — ``get_status`` reports ``running`` so /status keeps polling
        'processing' (correct: a retry is in flight), never the terminal 422."""
        await self.session.execute(
            update(QuizJob)
            .where(QuizJob.quiz_id == quiz_id)
            .values(
                status="running",
                last_error=(error or "")[:2000],
                # Epoch = unconditionally stale -> claimed on the next sweep.
                last_heartbeat_at=datetime(1970, 1, 1, tzinfo=timezone.utc),
            )
        )

    async def get_attempts(self, quiz_id: uuid.UUID) -> int | None:
        """Return the recovery ``attempts`` counter for a job, or None when no
        row exists. Used by the transient-vs-deterministic gate (Hitlist #8) to
        decide whether a transient in-process failure still has recovery budget."""
        job = await self.session.get(QuizJob, quiz_id)
        return int(job.attempts or 0) if job is not None else None

    def _dialect_name(self) -> str:
        """Best-effort dialect name (``postgresql`` / ``sqlite`` / ...). Used to
        gate the PG-only ``FOR UPDATE SKIP LOCKED`` claim path."""
        try:
            bind = self.session.get_bind()
            return getattr(getattr(bind, "dialect", None), "name", "") or ""
        except Exception:
            return ""

    async def get_status(self, quiz_id: uuid.UUID) -> str | None:
        """Return the durable job ``status`` (running/succeeded/failed) for a
        quiz, or None when no row exists. Cheap PK lookup used by /status to
        surface a terminally-failed run instead of polling 'processing' forever."""
        job = await self.session.get(QuizJob, quiz_id)
        return job.status if job is not None else None

    async def claim_stale(
        self, *, stale_after_s: int, max_attempts: int, limit: int = 10
    ) -> list[uuid.UUID]:
        """Atomically claim up to ``limit`` jobs stuck ``running`` past the
        heartbeat deadline and still under the attempt cap. Bumps the heartbeat
        AND ``attempts`` so a concurrent sweeper / the next cycle won't re-claim
        the same row immediately.

        Hitlist #1 (2026-06-30) — ``attempts`` is incremented HERE, at claim
        time, not only later in the re-run's ``mark_running``. Previously a
        re-run that died BEFORE ``mark_running`` (degraded Redis / malformed
        state blob / transient DB while loading state in ``_recover_one``) left
        ``attempts`` flat while freshly bumping the heartbeat, so the row stayed
        ``running`` and was re-claimed every ``stale_after_s`` FOREVER — an
        infinite re-claim loop re-spending LLM+FAL on every sweep. Bumping
        attempts at claim time guarantees ``fail_exhausted`` trips after
        ``max_attempts`` regardless of WHERE the re-run dies.

        DOUBLE-BUMP CAVEAT (review item D): a re-run that REACHES ``mark_running``
        bumps ``attempts`` a SECOND time (claim_stale +1, then mark_running +1),
        so for a recovery that gets that far the EFFECTIVE budget is ~``max_attempts
        / 2`` full re-runs, not ``max_attempts``. A re-run that dies before
        ``mark_running`` advances attempts by only +1, so it gets up to
        ``max_attempts`` claims. This converge-faster behaviour is intentional
        (it bounds re-spend strictly), but operators sizing ``max_attempts``
        should account for the ~2x factor — see the config comment on
        ``security.agent_recovery.max_attempts``.

        Multi-replica safety (audit P1): the recovery loop runs in every replica
        (up to ``apiMaxReplicas``), so two sweepers can fire ``claim_stale``
        against the same stale row concurrently. On Postgres we take a row lock
        with ``FOR UPDATE SKIP LOCKED`` on the candidate SELECT so a second
        sweeper transparently skips already-locked rows — exactly one grant. On
        sqlite (the test DB, and any backend without SKIP LOCKED) we fall back to
        the portable single-statement ``UPDATE ... WHERE quiz_id IN (subquery)
        RETURNING``: sqlite serialises writers, so the second claimer sees the
        heartbeat already bumped and the row no longer matches the staleness
        predicate — also exactly one grant.
        """
        deadline = datetime.now(timezone.utc) - timedelta(seconds=stale_after_s)
        now = datetime.now(timezone.utc)

        supports_skip_locked = self._dialect_name() in {"postgresql", "mysql", "mariadb"}

        if supports_skip_locked:
            # Phase 1: lock the candidate rows so a concurrent sweeper skips them.
            lock_stmt = (
                select(QuizJob.quiz_id)
                .where(
                    QuizJob.status == "running",
                    QuizJob.last_heartbeat_at < deadline,
                    QuizJob.attempts < max_attempts,
                )
                .order_by(QuizJob.last_heartbeat_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            locked = (await self.session.execute(lock_stmt)).scalars().all()
            if not locked:
                return []
            # Phase 2: bump the heartbeat AND attempts on exactly the rows we
            # locked (Hitlist #1 — attempts climbs even if the re-run dies before
            # mark_running, so fail_exhausted eventually trips).
            res = await self.session.execute(
                update(QuizJob)
                .where(QuizJob.quiz_id.in_(locked))
                .values(last_heartbeat_at=now, attempts=QuizJob.attempts + 1)
                .returning(QuizJob.quiz_id)
            )
            return [row[0] for row in res.fetchall()]

        # Portable atomic claim (sqlite / single-writer backends).
        sub = (
            select(QuizJob.quiz_id)
            .where(
                QuizJob.status == "running",
                QuizJob.last_heartbeat_at < deadline,
                QuizJob.attempts < max_attempts,
            )
            .order_by(QuizJob.last_heartbeat_at)
            .limit(limit)
        )
        res = await self.session.execute(
            update(QuizJob)
            .where(
                QuizJob.quiz_id.in_(sub),
                # Re-assert the staleness predicate on the UPDATE itself so a
                # racing claimer whose subquery materialised the same id cannot
                # re-grant a row whose heartbeat was already bumped.
                QuizJob.last_heartbeat_at < deadline,
            )
            # Bump heartbeat AND attempts (Hitlist #1 — see docstring).
            .values(last_heartbeat_at=now, attempts=QuizJob.attempts + 1)
            .returning(QuizJob.quiz_id)
        )
        return [row[0] for row in res.fetchall()]

    async def fail_exhausted(self, *, stale_after_s: int, max_attempts: int) -> list[uuid.UUID]:
        """Mark stale ``running`` jobs that have hit the attempt cap as failed."""
        deadline = datetime.now(timezone.utc) - timedelta(seconds=stale_after_s)
        res = await self.session.execute(
            update(QuizJob)
            .where(
                QuizJob.status == "running",
                QuizJob.last_heartbeat_at < deadline,
                QuizJob.attempts >= max_attempts,
            )
            .values(status="failed", last_error="max recovery attempts exceeded")
            .returning(QuizJob.quiz_id)
        )
        return [row[0] for row in res.fetchall()]
