# quafel social agent

A small, always-on local app (runs on the owner's Windows machine) that gives
**quafel** a witty X/Twitter presence:

- **Profile posts every 12h** — a ridiculous fake personality result with a
  *real, working* share link ("This morning, I'm a mid-century ballroom gown:
  https://quafel.com/result/…").
- **Replies up to 6×/day** — finds *recent* (last 4h only) personality-quiz
  chatter on X and replies with something silly ("Interesting. Perhaps you
  could use quafel.com to find out what type of duck you are!").
- **Never repeats itself** — thousands of posts can be pre-computed; every
  candidate is deduped against ALL past posts/replies, exactly *and*
  semantically.
- **Strong-judge gate** — nothing is posted until a gpt-4o-class judge
  approves it for quality, brand voice, and conscientiousness
  (refuse-by-default).
- **DRY-RUN by default** — until X API keys exist in `.env`, the app
  generates, judges, stores, and logs exactly what it *would* post, and posts
  nothing.

---

## Architecture

```
apps/social-agent/
├── social_agent/
│   ├── __main__.py     CLI: init-db | precompute | post-profile | reply-cycle |
│   │                        status | verify-share | serve
│   ├── config.py       .env loading; dry-run resolution (pure, tested)
│   ├── pipeline.py     orchestration: precompute / post cycle / reply cycle
│   ├── generator.py    content generation (gpt-4o-mini) — posts, replies, events
│   ├── judge.py        strong-judge prompts + refuse-by-default verdict parsing
│   ├── uniqueness.py   exact + semantic (cosine ≥ 0.85 rejected) dedup gate
│   ├── textutils.py    normalization, tweet-length budgeting (t.co = 23 chars)
│   ├── windowing.py    recency window (only last-4h posts are reply targets)
│   ├── visibility.py   visibility heuristic + sensitivity prefilter
│   ├── llm.py          OpenAI wrapper (chat/json, embeddings, web-search events)
│   ├── db.py           asyncpg + create-if-not-exists schema + repositories
│   ├── x_client.py     X API v2 client (OAuth 1.0a) + DryRunXClient
│   ├── oauth1.py       minimal OAuth 1.0a HMAC-SHA1 signer (tested vs X docs)
│   ├── search.py       pluggable search: x | fixture | none (tier fallback)
│   └── scheduler.py    long-running server mode (12h posts / 4h replies)
├── tests/              pure-logic unit tests — no network, no DB
├── fixtures/fake_tweets.json   demo targets for the reply pipeline
├── requirements.txt    httpx, asyncpg, openai, python-dotenv (lean by design)
└── .env.example        every knob, documented
```

### The gauntlet every text runs before posting

1. **Deterministic filters** (replies): recency window (last 4h only),
   visibility heuristic (skip < 50-follower accounts, > 150-reply threads,
   mega-viral posts), sensitivity keyword prefilter (grief/illness/politics
   never even reach the LLM).
2. **Uniqueness gate**: normalized exact match + embedding cosine vs **all**
   past posts and replies (> 0.85 = rejected). Backed by a partial UNIQUE
   index in Postgres so even a race can't slip a duplicate through.
3. **Strong judge** (`gpt-4o`): quality ≥ 7/10, on-brand (silly + fun,
   "quafel" lowercase), conscientious (would this land as insensitive to any
   plausible reader? for replies: is the *target post's nature* receptive to a
   joke at all?). Malformed output, missing fields, any uncertainty →
   **rejected**. Small models are deliberately NOT used for judging.
4. **Write budget**: hard monthly cap (default 450) under X free tier's ~500
   writes/month.
5. Only then: the X client — which in dry-run logs instead of posting.

Planned posts are **double-checked**: judged once at precompute time and
again at post time before going out.

### Storage (Azure Postgres — shared quizzical DB)

DDL lives in `backend/db/init/init.sql` *and* the app creates the tables at
startup (`ensure_schema`, idempotent):

- `social_posts` — every generated text: `kind` (post|reply), `status`
  (planned|posted|rejected|skipped), `text`, `text_norm`, `embedding
  VECTOR(384)`, `judge_verdicts JSONB`, `target_tweet_id`, `posted_at`, …
- `social_profiles` — synthetic shareable results minted by the bot; each
  references a real `session_history` row.
- `social_bot_state` — scheduler bookkeeping (survives restarts).

### Working share links

A profile post's link points at a real result page. At post time the bot
inserts a synthetic *completed* `session_history` row (title/description/
category generated together with the post text, so the page matches the
joke). That row is fully compatible with `GET /api/v1/result/{id}` and the
`/result-meta/{id}` OG-card endpoint, so `https://quafel.com/result/{id}`
renders and unfurls a share card on X. Bot-minted rows are marked with
`agent_plan = {"source": "social_bot", ...}` so they are always
distinguishable from real user sessions.

Verify any minted link against the live API:

```
python -m social_agent verify-share --id <session-uuid>
```

---

## X API tier reality (read this before buying anything)

| Capability | Free tier | Basic (~$200/mo) |
|---|---|---|
| POST /2/tweets (posts + replies) | ~500 writes/month | 3,000/mo |
| GET /2/tweets/search/recent (find posts to reply to) | **not available** | available |

Our full cadence is 2 posts + 6 replies/day ≈ **240 writes/month — the free
tier covers all posting**. But *finding* posts to reply to requires
recent search, which is **paid Basic tier only**. The search layer is
pluggable (`SOCIAL_SEARCH_MODE`):

- `x` — real X recent search (needs `X_BEARER_TOKEN` + Basic tier +
  `SOCIAL_X_SEARCH_ENABLED=true`).
- `fixture` — a JSON file of tweets (demos/testing; see
  `fixtures/fake_tweets.json`).
- `none` — **no-search fallback**: the bot runs posts-only and logs why.
- `auto` (default) — picks `x` if enabled+configured, else `fixture` if a
  path is set, else `none`.

So: **free tier = profile posts only; Basic = posts + replies.** The bot is
useful (and safe) either way.

## Run modes

```powershell
cd apps\social-agent
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env    # then fill in DATABASE_URL + OPENAI_API_KEY

.venv\Scripts\python -m social_agent init-db                  # create tables
.venv\Scripts\python -m social_agent precompute --count 500   # top up the pool
.venv\Scripts\python -m social_agent post-profile             # one 12h cycle
.venv\Scripts\python -m social_agent post-profile --event     # current-events flavored
.venv\Scripts\python -m social_agent reply-cycle              # one 4h cycle
.venv\Scripts\python -m social_agent status                   # inventory + cadence
.venv\Scripts\python -m social_agent serve                    # long-running server
```

`serve` is a single process with an internal scheduler (profile post every
12h, reply cycle every 4h, jittered so it doesn't post at robotic exact
times; last-run bookkeeping is in Postgres so restarts never double-post).
One-shot commands exist so **Windows Task Scheduler** can drive the same
cadence instead.

### Windows Task Scheduler (alternative to `serve`)

Run as the logged-in user (adjust the path if the repo lives elsewhere):

```powershell
schtasks /create /tn "quafel-social-post" /sc DAILY /st 09:00 /ri 720 /du 24:00 `
  /tr "\"C:\Users\Yeyian PC\Desktop\quizzical\quizzical\apps\social-agent\.venv\Scripts\python.exe\" -m social_agent post-profile" `
  /f
schtasks /create /tn "quafel-social-reply" /sc DAILY /st 08:00 /ri 240 /du 24:00 `
  /tr "\"C:\Users\Yeyian PC\Desktop\quizzical\quizzical\apps\social-agent\.venv\Scripts\python.exe\" -m social_agent reply-cycle" `
  /f
```

(`/ri 720` = every 12h, `/ri 240` = every 4h.) One catch: Task Scheduler's
working directory defaults to System32, and the app resolves `.env` relative
to its own folder — which works from anywhere — but `SOCIAL_FIXTURE_PATH`
style relative paths should be absolute in `.env` when using Task Scheduler.
Delete with `schtasks /delete /tn "quafel-social-post" /f` (same for reply).

**Dry-run:** automatic while any of the four X keys is missing. With keys
present the bot goes live; set `SOCIAL_DRY_RUN=true` (or pass `--dry-run`) to
force dry-run anyway.

**Current events** (`SOCIAL_EVENTS_ENABLED=true` or `post-profile --event`):
asks OpenAI's web-search tool for one lighthearted, globally recognizable
event happening now (sports tournament, awards show, holiday) and themes the
post on it, tagging the row with `event_tag`. Strictly optional and
pluggable; any failure falls back to the normal planned pool.

## Tests

Pure-logic unit tests (uniqueness gate, window filtering, visibility
heuristic, judge-verdict parsing, dry-run behavior, OAuth1 signing vs the
documented X test vector) — no network, no DB:

```powershell
cd apps\social-agent
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

(Also runnable with the backend venv's python from this directory.)

## Dry-run transcripts

Captured against production PG on 2026-07-02 with no X keys configured
(dry-run auto-engaged). Nothing was posted to X.

### Profile-post cycle (`python -m social_agent post-profile`)

The bot took the oldest planned post, re-judged it at post time, minted a
real shareable result page, and logged what it would have posted:

```
2026-07-02 08:36:03 INFO social_agent.x: [DRY-RUN] would POST tweet:
I just found out I'm a ginger root. I am zesty, a little spicy, and everyone's go-to for
cozy dishes. Gardeners love me, chefs adore me. https://quafel.com/result/6df7a590-d67d-4270-b503-d86f1b8a0089
2026-07-02 08:36:03 INFO social_agent.pipeline: [DRY-RUN] post cycle complete — row stays planned; nothing posted
{
  "posted": false,
  "dry_run": true,
  "post_id": "5a090891-e2d0-421b-9c43-d0c358894741",
  "session_id": "6df7a590-d67d-4270-b503-d86f1b8a0089",
  "share_url": "https://quafel.com/result/6df7a590-d67d-4270-b503-d86f1b8a0089",
  "would_post_text": "I just found out I'm a ginger root. I am zesty, a little spicy, and
                      everyone's go-to for cozy dishes. Gardeners love me, chefs adore me.
                      https://quafel.com/result/6df7a590-d67d-4270-b503-d86f1b8a0089",
  "judge": { "approve": true, "quality": 8, "on_brand": true, "conscientious": true,
             "reason": "Witty and fun, fits the brand voice, and is conscientious." }
}
```

The minted share link is real and live-verified:

```
$ python -m social_agent verify-share --id 6df7a590-d67d-4270-b503-d86f1b8a0089
GET https://api-quizzical-dev.../api/v1/result/6df7a590-d67d-4270-b503-d86f1b8a0089 -> 200
{
  "title": "Ginger Root",
  "description": "You're the zesty hero of the kitchen! Known for adding a splash of flavor,
                  you bring warmth and spiciness to every gathering.",
  "category": "Food",
  ...
}
```

### Reply cycle (`python -m social_agent reply-cycle`, fixture search provider)

Recent-search needs the paid X tier, so this demo uses
`SOCIAL_FIXTURE_PATH=fixtures/fake_tweets.json` (six synthetic tweets built to
exercise every filter; entries with an empty `created_at` count as "just
posted"). Every gate is visible in the output:

```
2026-07-02 08:36:36 INFO social_agent.pipeline: reply targets: 2 kept, 4 skipped
2026-07-02 08:36:39 INFO social_agent.x: [DRY-RUN] would REPLY to 1940000000000000001:
Ah, the classic INFP dilemma: a heart full of dreams and a closet full of half-finished
yarn monsters. Ever thought of a quafel.com project to master the art of finishing?!
{
  "replied": 1,
  "provider": "fixture",
  "dry_run": true,
  "results": [
    {
      "tweet_id": "1940000000000000001",
      "outcome": "[DRY-RUN] would reply",
      "text": "Ah, the classic INFP dilemma: a heart full of dreams and a closet full of
               half-finished yarn monsters. Ever thought of a quafel.com project to master
               the art of finishing?!",
      "judge": { "approve": true, "quality": 8, "on_brand": true, "conscientious": true,
                 "relevant": true,
                 "reason": "The reply is witty, relevant to the target post, and maintains
                            the playful, self-deprecating tone of quafel." }
    }
  ],
  "skipped": [
    { "tweet_id": "...002", "reason": "sensitivity prefilter: death, grief" },
    { "tweet_id": "...003", "reason": "visibility: 4823 replies (> 150): ours would be buried" },
    { "tweet_id": "...004", "reason": "visibility: author has 4 followers (< 50): zero-visibility" },
    { "tweet_id": "...005", "reason": "outside recency window" }
  ]
}
```

Note the grief-adjacent tweet never even reached the LLM (deterministic
prefilter), the buried/invisible/stale ones were skipped for visibility and
recency, and the one posted reply is personalized to the target's actual
content — with the judge's verdict stored alongside it in `social_posts`.

## Operational notes

- The LLM cost of a 200-post precompute run is well under $1 (generation on
  gpt-4o-mini, judging batched on gpt-4o, embeddings on
  text-embedding-3-small at 384 dims). `precompute --budget` hard-caps spend.
- Rejected texts stay in `social_posts` (status `rejected`) with the judge's
  verdict JSON — a free audit trail and negative-example corpus.
- The monthly write cap is computed from `posted_at` in the DB, so it holds
  across restarts and across server/Task-Scheduler modes.
- Never commit `.env` (gitignored). Keys live in
  `quizzical-shared-kv` (`database-url`, `openai-api-key`); X keys exist only
  in the owner's X developer portal.
- The backend has a session-retention helper
  (`SessionRepository.purge_older_than`) that is currently NOT scheduled. If
  a retention cron is ever enabled, exempt bot rows
  (`agent_plan->>'source' = 'social_bot'`) or posted share links will 404
  after the retention window (and `social_profiles` rows cascade-delete).
