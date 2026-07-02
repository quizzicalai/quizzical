"""Postgres storage (Azure PG, same database as the quizzical backend).

Tables (DDL mirrored in backend/db/init/init.sql — keep in sync):
- social_profiles  : synthetic shareable quiz results minted by the bot
- social_posts     : every planned / posted / rejected post & reply
- social_bot_state : tiny key/value store for scheduler bookkeeping

The app also creates these at startup (idempotent) so the bot works against a
database that predates the init.sql addition.

Synthetic result rows inserted into session_history are marked with
agent_plan = {"source": "social_bot"} so they are always distinguishable from
real user sessions (plus social_profiles.session_id references them).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import asyncpg

SOCIAL_BOT_MARKER = {"source": "social_bot", "app": "apps/social-agent", "version": 1}

DDL = """
CREATE TABLE IF NOT EXISTS social_profiles (
  id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  session_id   UUID NOT NULL UNIQUE REFERENCES session_history(session_id) ON DELETE CASCADE,
  title        TEXT NOT NULL CHECK (title <> ''),
  description  TEXT NOT NULL,
  category     TEXT NOT NULL,
  share_url    TEXT NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS social_posts (
  id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  kind              TEXT NOT NULL CHECK (kind IN ('post','reply')),
  status            TEXT NOT NULL DEFAULT 'planned'
                    CHECK (status IN ('planned','posted','rejected','skipped')),
  text              TEXT NOT NULL CHECK (text <> ''),
  text_norm         TEXT NOT NULL,
  posted_text       TEXT NULL,
  profile_id        UUID NULL REFERENCES social_profiles(id) ON DELETE SET NULL,
  profile_payload   JSONB NULL,
  target_tweet_id   TEXT NULL,
  target_tweet_text TEXT NULL,
  target_author     TEXT NULL,
  event_tag         TEXT NULL,
  judge_verdicts    JSONB NULL,
  embedding         VECTOR(384) NULL,
  posted_tweet_id   TEXT NULL,
  posted_at         TIMESTAMPTZ NULL,
  rejected_reason   TEXT NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS social_bot_state (
  key         TEXT PRIMARY KEY,
  value       JSONB NOT NULL,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_social_posts_text_norm
  ON social_posts (text_norm) WHERE status <> 'rejected';

CREATE UNIQUE INDEX IF NOT EXISTS uq_social_posts_reply_target
  ON social_posts (target_tweet_id)
  WHERE kind = 'reply' AND target_tweet_id IS NOT NULL AND status <> 'rejected';

CREATE INDEX IF NOT EXISTS idx_social_posts_kind_status
  ON social_posts (kind, status, created_at);

CREATE INDEX IF NOT EXISTS idx_social_posts_posted_at
  ON social_posts (posted_at) WHERE posted_at IS NOT NULL;
"""


def vec_literal(embedding: list[float] | None) -> str | None:
    if embedding is None:
        return None
    return "[" + ",".join(f"{x:.7g}" for x in embedding) + "]"


def vec_parse(text: str | None) -> list[float] | None:
    if not text:
        return None
    return [float(x) for x in text.strip("[]").split(",") if x]


async def connect_pool(dsn: str) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn, min_size=1, max_size=4, command_timeout=60)


async def ensure_schema(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        # Extensions normally exist already (backend init.sql); tolerate
        # missing privileges since we only need them if truly absent.
        for ext in ("vector", "uuid-ossp"):
            try:
                await conn.execute(f'CREATE EXTENSION IF NOT EXISTS "{ext}"')
            except (asyncpg.PostgresError, asyncpg.exceptions.InsufficientPrivilegeError):
                pass
        await conn.execute(DDL)


# --------------------------------------------------------------------------
# Dedup corpus
# --------------------------------------------------------------------------

async def load_dedup_corpus(pool: asyncpg.Pool) -> tuple[set[str], list[tuple[list[float], str]]]:
    """All non-rejected texts (norms + embeddings) — the uniqueness universe."""
    rows = await pool.fetch(
        "SELECT text, text_norm, embedding::text AS emb FROM social_posts "
        "WHERE status <> 'rejected'"
    )
    norms = {r["text_norm"] for r in rows}
    embs: list[tuple[list[float], str]] = []
    for r in rows:
        v = vec_parse(r["emb"])
        if v:
            embs.append((v, r["text"]))
    return norms, embs


# --------------------------------------------------------------------------
# social_posts
# --------------------------------------------------------------------------

async def insert_post(
    pool: asyncpg.Pool,
    *,
    kind: str,
    status: str,
    text: str,
    text_norm: str,
    embedding: list[float] | None = None,
    profile_payload: dict | None = None,
    target_tweet_id: str | None = None,
    target_tweet_text: str | None = None,
    target_author: str | None = None,
    event_tag: str | None = None,
    judge_verdicts: dict | list | None = None,
    rejected_reason: str | None = None,
) -> uuid.UUID | None:
    """Insert a row; returns id, or None when the partial-unique index says
    the text already exists non-rejected (race-safe exact dedup)."""
    row = await pool.fetchrow(
        """
        INSERT INTO social_posts
          (kind, status, text, text_norm, embedding, profile_payload,
           target_tweet_id, target_tweet_text, target_author, event_tag,
           judge_verdicts, rejected_reason)
        VALUES ($1, $2, $3, $4, $5::vector, $6, $7, $8, $9, $10, $11, $12)
        ON CONFLICT (text_norm) WHERE status <> 'rejected' DO NOTHING
        RETURNING id
        """,
        kind, status, text, text_norm, vec_literal(embedding),
        json.dumps(profile_payload) if profile_payload is not None else None,
        target_tweet_id, target_tweet_text, target_author, event_tag,
        json.dumps(judge_verdicts) if judge_verdicts is not None else None,
        rejected_reason,
    )
    return row["id"] if row else None


async def next_planned_post(pool: asyncpg.Pool, prefer_event: bool = False) -> asyncpg.Record | None:
    """Next planned standalone post.

    Normal path: FIFO through the precomputed evergreen pool (event-tagged
    posts are EXCLUDED — an event post published weeks later reads as botty).
    ``prefer_event=True``: first try the freshest event-tagged post from the
    last 6 hours (the one the flavored cycle just generated), then fall back
    to the evergreen FIFO.
    """
    if prefer_event:
        row = await pool.fetchrow(
            "SELECT id, text, profile_payload, event_tag, profile_id FROM social_posts "
            "WHERE kind = 'post' AND status = 'planned' AND event_tag IS NOT NULL "
            "AND created_at > now() - interval '6 hours' "
            "ORDER BY created_at DESC LIMIT 1"
        )
        if row:
            return row
    return await pool.fetchrow(
        "SELECT id, text, profile_payload, event_tag, profile_id FROM social_posts "
        "WHERE kind = 'post' AND status = 'planned' AND event_tag IS NULL "
        "ORDER BY created_at ASC LIMIT 1"
    )


async def expire_stale_event_posts(pool: asyncpg.Pool, max_age_hours: int = 48) -> int:
    """Event-flavored posts that never went out go stale fast; skip them so
    they can never surface weeks after the moment has passed."""
    result = await pool.execute(
        "UPDATE social_posts SET status = 'skipped', "
        "rejected_reason = 'stale event post (event window passed)', "
        "last_updated_at = now() "
        "WHERE kind = 'post' AND status = 'planned' AND event_tag IS NOT NULL "
        "AND created_at < now() - ($1 || ' hours')::interval",
        str(max_age_hours),
    )
    try:
        return int(result.split()[-1])
    except (ValueError, IndexError, AttributeError):
        return 0


async def get_profile(pool: asyncpg.Pool, profile_id: uuid.UUID) -> asyncpg.Record | None:
    return await pool.fetchrow(
        "SELECT id, session_id, title, description, category, share_url "
        "FROM social_profiles WHERE id = $1",
        profile_id,
    )


async def sample_banked_topics(pool: asyncpg.Pool, k: int = 12) -> list[str]:
    """Random sample of witty topics from the banked post pool (categories +
    profile titles) for the topic-led discovery direction."""
    rows = await pool.fetch(
        """
        SELECT topic FROM (
            SELECT DISTINCT topic FROM (
                SELECT profile_payload->>'category' AS topic FROM social_posts
                 WHERE kind = 'post' AND profile_payload IS NOT NULL
                UNION ALL
                SELECT profile_payload->>'title' FROM social_posts
                 WHERE kind = 'post' AND profile_payload IS NOT NULL
            ) raw
            WHERE topic IS NOT NULL AND topic <> ''
        ) dedup
        ORDER BY random() LIMIT $1
        """,
        k,
    )
    return [r["topic"] for r in rows]


async def mark_posted(
    pool: asyncpg.Pool,
    post_id: uuid.UUID,
    *,
    posted_text: str,
    posted_tweet_id: str | None,
    profile_id: uuid.UUID | None = None,
) -> None:
    await pool.execute(
        "UPDATE social_posts SET status = 'posted', posted_text = $2, "
        "posted_tweet_id = $3, profile_id = COALESCE($4, profile_id), "
        "posted_at = now(), last_updated_at = now() WHERE id = $1",
        post_id, posted_text, posted_tweet_id, profile_id,
    )


async def mark_status(
    pool: asyncpg.Pool,
    post_id: uuid.UUID,
    status: str,
    reason: str | None = None,
) -> None:
    await pool.execute(
        "UPDATE social_posts SET status = $2, rejected_reason = COALESCE($3, rejected_reason), "
        "last_updated_at = now() WHERE id = $1",
        post_id, status, reason,
    )


async def attach_profile(pool: asyncpg.Pool, post_id: uuid.UUID, profile_id: uuid.UUID) -> None:
    await pool.execute(
        "UPDATE social_posts SET profile_id = $2, last_updated_at = now() WHERE id = $1",
        post_id, profile_id,
    )


async def already_replied_target(pool: asyncpg.Pool, tweet_id: str) -> bool:
    row = await pool.fetchrow(
        "SELECT 1 FROM social_posts WHERE kind = 'reply' AND target_tweet_id = $1 "
        "AND status <> 'rejected' LIMIT 1",
        tweet_id,
    )
    return row is not None


async def author_in_cooldown(pool: asyncpg.Pool, author: str, days: int) -> bool:
    if not author:
        return False
    row = await pool.fetchrow(
        "SELECT 1 FROM social_posts WHERE kind = 'reply' AND target_author = $1 "
        "AND status <> 'rejected' AND created_at > now() - ($2 || ' days')::interval LIMIT 1",
        author, str(days),
    )
    return row is not None


async def writes_this_month(pool: asyncpg.Pool) -> int:
    row = await pool.fetchrow(
        "SELECT count(*) AS n FROM social_posts WHERE status = 'posted' "
        "AND posted_at >= date_trunc('month', now())"
    )
    return int(row["n"])


async def stats(pool: asyncpg.Pool) -> dict[str, Any]:
    rows = await pool.fetch(
        "SELECT kind, status, count(*) AS n FROM social_posts GROUP BY kind, status"
    )
    profiles = await pool.fetchrow("SELECT count(*) AS n FROM social_profiles")
    out: dict[str, Any] = {"posts": {}, "profiles": int(profiles["n"])}
    for r in rows:
        out["posts"][f"{r['kind']}/{r['status']}"] = int(r["n"])
    out["writes_this_month"] = await writes_this_month(pool)
    return out


# --------------------------------------------------------------------------
# Synthetic shareable profiles (session_history + social_profiles)
# --------------------------------------------------------------------------

async def mint_profile(
    pool: asyncpg.Pool,
    *,
    title: str,
    description: str,
    category: str,
    site_base: str,
    image_url: str | None = None,
) -> tuple[uuid.UUID, uuid.UUID, str]:
    """Insert a synthetic COMPLETED session compatible with
    GET /api/v1/result/{id} (+ /result-meta/{id}), and its social_profiles row.

    Returns (profile_id, session_id, share_url).
    """
    session_id = uuid.uuid4()
    share_url = f"{site_base}/result/{session_id}"
    now = datetime.now(timezone.utc)

    final_result = {
        "title": title,
        "description": description,
        "image_url": image_url,
    }
    synopsis = {
        "type": "synopsis",
        "title": category,
        "summary": f"A quafel personality result: {title}",
    }
    transcript = [
        {
            "role": "system",
            "content": "synthetic shareable result minted by apps/social-agent (social bot)",
        }
    ]

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO session_history
                  (session_id, category, category_synopsis, agent_plan,
                   session_transcript, character_set, final_result,
                   is_completed, completed_at)
                VALUES ($1, $2, $3::jsonb, $4::jsonb, $5::jsonb, '[]'::jsonb,
                        $6::jsonb, TRUE, $7)
                """,
                session_id, category, json.dumps(synopsis),
                json.dumps(SOCIAL_BOT_MARKER), json.dumps(transcript),
                json.dumps(final_result), now,
            )
            row = await conn.fetchrow(
                """
                INSERT INTO social_profiles (session_id, title, description, category, share_url)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id
                """,
                session_id, title, description, category, share_url,
            )
    return row["id"], session_id, share_url


# --------------------------------------------------------------------------
# social_bot_state
# --------------------------------------------------------------------------

async def state_get(pool: asyncpg.Pool, key: str) -> Any:
    row = await pool.fetchrow("SELECT value FROM social_bot_state WHERE key = $1", key)
    return json.loads(row["value"]) if row else None


async def state_set(pool: asyncpg.Pool, key: str, value: Any) -> None:
    await pool.execute(
        "INSERT INTO social_bot_state (key, value, updated_at) VALUES ($1, $2::jsonb, now()) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
        key, json.dumps(value),
    )
