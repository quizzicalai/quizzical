"""Crash-recovery sweeper for stalled live agent jobs (``quiz_jobs``).

Agent work runs in-process via FastAPI BackgroundTasks. A web-process death
(deploy / OOM / Container Apps scale-in) kills the in-flight run, leaving its
``quiz_jobs`` row ``running`` with a stale heartbeat — and the user's quiz stuck
``processing`` forever. This sweeper re-runs those jobs, resuming from the
Redis live state (or rebuilding from the durable Postgres snapshot if Redis is
also gone), so the quiz completes. The DB-level atomic claim makes the sweep
safe across multiple replicas. Re-spend is bounded by the per-session action
cap and ``max_attempts``.
"""
from __future__ import annotations

import asyncio

import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)


def _cfg():
    return getattr(getattr(settings, "security", None), "agent_recovery", None)


async def _recover_one(quiz_id, agent_graph, redis_client) -> None:
    # Local imports avoid a circular import (quiz endpoint imports services).
    from app.api import dependencies as deps
    from app.api.endpoints.quiz import _rehydrate_state_from_db, run_agent_in_background
    from app.services.database import QuizJobRepository
    from app.services.redis_cache import CacheRepository

    state = await CacheRepository(redis_client).get_quiz_state(quiz_id)
    if state is None:
        # Redis lost the live state too — rebuild from the durable DB snapshot.
        factory = deps.async_session_factory
        rstate = None
        if factory is not None:
            async with factory() as db:
                rstate = await _rehydrate_state_from_db(db, quiz_id)
        if rstate is None:
            if factory is not None:
                async with factory() as db:
                    await QuizJobRepository(db).mark_failed(quiz_id, "no recoverable state")
                    await db.commit()
            logger.info("agent_recovery.skip_no_state", quiz_id=str(quiz_id))
            return
        state = rstate
        # Re-prime Redis with the DB-rebuilt state BEFORE re-running the agent.
        # run_agent_in_background's final persistence is now a field-scoped
        # atomic MERGE (audit P1) which no-ops when the key is missing; without
        # a live key to merge into, the re-run's results would not land in the
        # cache (the full-SET fallback covers it, but priming here mirrors the
        # /status rehydrate reprime and gives /next/status a live key during the
        # re-run too). Best-effort: a cache fault must not abort recovery.
        try:
            await CacheRepository(redis_client).save_quiz_state(rstate)
        except Exception:
            logger.debug("agent_recovery.reprime_failed", quiz_id=str(quiz_id))

    logger.info("agent_recovery.rerun", quiz_id=str(quiz_id))
    # run_agent_in_background re-marks the job running (attempts++) and on
    # completion marks succeeded/failed; the FE's normal polling then succeeds.
    await run_agent_in_background(state, redis_client, agent_graph)


async def sweep_once(app) -> int:
    """Recover up to ``batch`` stalled jobs. Returns the number claimed."""
    from app.api import dependencies as deps
    from app.services.database import QuizJobRepository

    cfg = _cfg()
    if cfg is None or not getattr(cfg, "enabled", False):
        return 0
    agent_graph = getattr(app.state, "agent_graph", None)
    factory = deps.async_session_factory
    if agent_graph is None or factory is None:
        return 0

    stale_after_s = int(getattr(cfg, "stale_after_s", 180))
    max_attempts = int(getattr(cfg, "max_attempts", 3))
    batch = int(getattr(cfg, "batch", 5))

    async with factory() as db:
        repo = QuizJobRepository(db)
        await repo.fail_exhausted(stale_after_s=stale_after_s, max_attempts=max_attempts)
        await db.commit()
        claimed = await repo.claim_stale(
            stale_after_s=stale_after_s, max_attempts=max_attempts, limit=batch
        )
        await db.commit()

    if not claimed:
        return 0
    logger.info("agent_recovery.claimed", count=len(claimed))

    try:
        redis_client = deps.get_redis_client()
    except Exception:
        logger.warning("agent_recovery.no_redis", exc_info=True)
        return 0

    for qid in claimed:
        try:
            await _recover_one(qid, agent_graph, redis_client)
        except Exception:
            logger.warning("agent_recovery.rerun_failed", quiz_id=str(qid), exc_info=True)
    return len(claimed)


async def recovery_loop(app) -> None:
    """Background task (started in lifespan): periodic recovery sweep."""
    cfg = _cfg()
    if cfg is None or not getattr(cfg, "enabled", False):
        return
    interval = int(getattr(cfg, "interval_s", 60))
    try:
        # Sweep shortly after startup to recover orphans from a prior instance
        # that died mid-run, then on the configured interval.
        await asyncio.sleep(min(15, interval))
        while True:
            try:
                await sweep_once(app)
            except Exception:
                logger.warning("agent_recovery.sweep_error", exc_info=True)
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        logger.info("agent_recovery.loop_cancelled")
        raise
