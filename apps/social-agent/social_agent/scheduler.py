"""Long-running local server mode: one process, internal scheduler.

Cadence (owner requirements): profile post every 12h, reply cycle every 4h.
Last-run bookkeeping lives in social_bot_state (survives restarts), so a
restart never double-posts and Windows Task Scheduler one-shots can coexist
with server mode.
"""
from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone

import asyncpg

from . import db
from .config import Settings
from .llm import LLMClient
from .pipeline import run_post_cycle, run_reply_cycle
from .search import SearchProvider

log = logging.getLogger("social_agent.scheduler")

_POST_KEY = "last_post_cycle_at"
_REPLY_KEY = "last_reply_cycle_at"
_TICK_SECONDS = 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _last_run(pool: asyncpg.Pool, key: str) -> datetime | None:
    value = await db.state_get(pool, key)
    if not value:
        return None
    try:
        return datetime.fromisoformat(value["at"])
    except (KeyError, TypeError, ValueError):
        return None


async def _mark_run(pool: asyncpg.Pool, key: str) -> None:
    await db.state_set(pool, key, {"at": _now().isoformat()})


def _due(last: datetime | None, every_hours: float, now: datetime) -> bool:
    if last is None:
        return True
    return (now - last).total_seconds() >= every_hours * 3600


async def serve(
    pool: asyncpg.Pool,
    llm: LLMClient,
    settings: Settings,
    x_client,
    provider: SearchProvider,
) -> None:
    """Run forever; checks due-ness every minute with a little jitter so the
    account never posts at robotic exact intervals."""
    log.info(
        "social agent serving (dry_run=%s, search=%s): posts every %.0fh, replies every %.0fh",
        x_client.dry_run, provider.name, settings.post_every_hours, settings.reply_every_hours,
    )
    post_jitter = random.uniform(0, 20 * 60)
    reply_jitter = random.uniform(0, 10 * 60)
    while True:
        try:
            now = _now()
            last_post = await _last_run(pool, _POST_KEY)
            if _due(last_post, settings.post_every_hours + post_jitter / 3600, now):
                log.info("post cycle due — running")
                result = await run_post_cycle(
                    pool, llm, settings, x_client, provider_name=provider.name
                )
                log.info("post cycle result: %s", result)
                await _mark_run(pool, _POST_KEY)
                post_jitter = random.uniform(0, 20 * 60)

            last_reply = await _last_run(pool, _REPLY_KEY)
            if _due(last_reply, settings.reply_every_hours + reply_jitter / 3600, now):
                log.info("reply cycle due — running")
                result = await run_reply_cycle(pool, llm, settings, x_client, provider)
                log.info("reply cycle result: %s", result)
                await _mark_run(pool, _REPLY_KEY)
                reply_jitter = random.uniform(0, 10 * 60)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a bad cycle must not kill the server
            log.exception("cycle failed; will retry on next due tick")
        await asyncio.sleep(_TICK_SECONDS)
