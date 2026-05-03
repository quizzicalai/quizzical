# Quizzical Backend

The Quizzical backend is an async FastAPI service that drives quiz generation, session state, result retrieval, and feedback collection for the Quizzical application.

It is not a thin CRUD API. The service coordinates a multi-step LangGraph workflow, persists durable session data in PostgreSQL, stores live graph state in Redis, and exposes a polling-based quiz lifecycle that the frontend can drive safely.

## Current Responsibilities

- Start a quiz from a free-form topic or category.
- Generate a synopsis and, when time budget allows, an initial character set during the initial request.
- Continue quiz generation asynchronously after the client explicitly proceeds.
- Accept answers, update session history, and continue question generation in the background.
- Return either the next unseen question, a processing state, or the final result.
- Persist shareable completed results and user feedback.
- Expose frontend configuration, health, readiness, and generated trace headers for observability.

## Stack

- Python 3.11+
- FastAPI
- LangGraph
- PostgreSQL via SQLAlchemy async engine
- Redis via redis.asyncio
- Pydantic v2
- Uvicorn
- Structlog
- Optional OpenTelemetry instrumentation

## Runtime Architecture

The service is composed of four main layers:

1. API layer in `app/main.py` and `app/api/endpoints/*`
2. Agent orchestration in `app/agent/*`
3. Persistence services in `app/services/*`
4. Configuration and dependency wiring in `app/core/*` and `app/api/dependencies.py`

### Request Flow

1. The FastAPI lifespan hook initializes the SQLAlchemy engine, Redis pool, and compiled LangGraph instance.
2. `POST /quiz/start` creates a new session, invokes the graph for the first step, and requires a synopsis before returning.
3. The current graph state is stored in Redis under `quiz_session:{session_id}` with a 1 hour TTL.
4. A durable session row is upserted into PostgreSQL as soon as synopsis data exists.
5. `POST /quiz/proceed` and `POST /quiz/next` schedule background graph execution and immediately return `202 Accepted`.
6. `GET /quiz/status/{quiz_id}` polls Redis-backed state until a new question or final result is ready.
7. Completed results and accumulated QA history are persisted back to PostgreSQL.

### Agent Lifecycle

The current graph is deliberately split into two phases:

1. Preparation phase: normalize and analyze the requested topic, generate a synopsis, build a candidate character list, and draft character profiles with a batch-first strategy plus per-item fallback.
2. Gated quiz phase: wait until the API sets `ready_for_questions=True`, generate baseline questions, generate additional questions after answers arrive, and produce a final result once the graph decides the quiz is complete.

This matters operationally because `start` is user-facing and time-bounded, while `proceed` and `next` are intentionally asynchronous.

## API Surface

Business routes are mounted under `settings.project.api_prefix`. The checked-in local sample config uses `/api/v1`, while the hardcoded fallback default is `/api`.

Health endpoints are not prefixed.

### Top-Level Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/` | Redirects to FastAPI docs |
| `GET` | `/health` | Liveness probe; always cheap and returns `200` when the app is running |
| `GET` | `/readiness` | Readiness probe; checks DB and Redis when initialized and returns `503` on dependency failure |

### Business Endpoints

Assume `${API_PREFIX}` means the configured API prefix, for example `/api/v1`.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `${API_PREFIX}/config` | Serves frontend-facing configuration derived from YAML and environment overrides |
| `POST` | `${API_PREFIX}/quiz/start` | Creates a new quiz session, generates synopsis, and may also return characters |
| `POST` | `${API_PREFIX}/quiz/proceed` | Opens the question-generation gate and schedules background work |
| `POST` | `${API_PREFIX}/quiz/next` | Records an answer and schedules additional background generation |
| `GET` | `${API_PREFIX}/quiz/status/{quiz_id}` | Returns `processing`, the next active question, or the finished result |
| `GET` | `${API_PREFIX}/quiz/{quiz_id}/media` | Snapshot of asynchronously-generated FAL image URLs (synopsis, characters, final result). Always returns `200`; empty fields when nothing has been persisted yet. |
| `GET` | `${API_PREFIX}/result/{result_id}` | Returns a persisted shareable result for a completed session |
| `POST` | `${API_PREFIX}/feedback` | Stores thumbs-up or thumbs-down feedback with an optional comment |

### Error Envelope

All error responses (4xx and 5xx) share a unified JSON envelope produced by `app/core/errors.py`:

```json
{
  "detail":    "Human-readable summary",
  "errorCode": "STABLE_MACHINE_CODE",
  "traceId":   "echo of X-Trace-ID for log correlation",
  "details":   { "...": "optional structured context, omitted when null" }
}
```

Stable `errorCode` values include `BAD_REQUEST`, `UNAUTHORIZED`, `FORBIDDEN`, `NOT_FOUND`, `CONFLICT`, `SESSION_BUSY`, `PAYLOAD_TOO_LARGE`, `VALIDATION_ERROR`, `RATE_LIMITED`, `INTERNAL_SERVER_ERROR`, and `SERVICE_UNAVAILABLE`. Endpoints raise `AppError` subclasses (`SessionBusyError`, `NotFoundError`, etc.) which the global handler renders into the envelope; `HTTPException`, `RequestValidationError`, and uncaught `Exception` are also wrapped uniformly.

#### Internal helpers

- `app/core/coercion.py::coerce_to_dict(obj) -> dict[str, Any]` — single canonical helper used by `app/api/endpoints/quiz.py` to normalise Pydantic models, dicts, and `None` into plain dicts before serialising payloads. Logs `coercion.model_dump.fail` / `coercion.model_dump.non_dict` at debug level when a model's `model_dump()` raises or returns a non-dict, and returns `{}` rather than crashing the request.
- Optional agent tools in `app/agent/graph.py` and `app/agent/canonical_sets.py` use narrow `except ImportError` guards (not bare `except Exception`) and emit a single `agent.optional_tool_unavailable` info log on startup so missing-tool fallbacks are observable.

## Quiz Session Contract

### Start Quiz

`POST ${API_PREFIX}/quiz/start`

Request body:

```json
{
	"category": "Which fantasy class am I?",
	"cf-turnstile-response": "token-from-client"
}
```

Response shape:

```json
{
	"quizId": "6f4f9a2a-9f61-4303-a7fa-6ef8b0d4e9f2",
	"initialPayload": {
		"type": "synopsis",
		"data": {
			"type": "synopsis",
			"title": "Quiz: Which Fantasy Class Are You?",
			"summary": "A short synopsis for the quiz."
		}
	},
	"charactersPayload": {
		"type": "characters",
		"data": [
			{
				"name": "Mage",
				"shortDescription": "Curious and analytical.",
				"profileText": "Longer profile text.",
				"imageUrl": null
			}
		]
	}
}
```

Important implementation details:

- The endpoint returns `201 Created`.
- A synopsis is required for success. If the first graph step does not produce one, the request fails.
- Character generation is attempted within a separate stream budget. If it exceeds that budget, the response can still succeed with synopsis-only payloads.
- Per-character LLM fan-out is bounded by `quiz.character_concurrency` (default `8`, capped per AC-PERF-CHAR-2). Higher values empirically trigger Gemini `503 ServiceUnavailable` cascades on quizzes with ≥ 13 archetypes — leaving ~40 % of profiles empty after retry exhaustion.
- Characters whose `profile_text` is empty or whitespace-only are dropped from the response, the persisted snapshot, and the image-generation queue (AC-START-11). This prevents a single failed-LLM character from violating the `characters_profile_text_check` Postgres CHECK constraint and rolling back the entire `session_history` insert (which would silently break the `/quiz/{id}/media` polling endpoint for the rest of the session). When at least one character is dropped, a single `start_quiz.characters.filtered_empty` warning is emitted with the dropped names (AC-START-12).
- The initial graph state is cached in Redis and the initial session snapshot is persisted to PostgreSQL.

### Proceed Quiz

`POST ${API_PREFIX}/quiz/proceed`

Request body:

```json
{
	"quizId": "6f4f9a2a-9f61-4303-a7fa-6ef8b0d4e9f2"
}
```

Response shape:

```json
{
	"status": "processing",
	"quizId": "6f4f9a2a-9f61-4303-a7fa-6ef8b0d4e9f2"
}
```

Current behavior:

- Reads the cached state from Redis.
- Sets `ready_for_questions=true`.
- Saves that state back to Redis before scheduling background graph execution.
- Returns immediately with `202 Accepted`.

### Submit an Answer

`POST ${API_PREFIX}/quiz/next`

Request body:

```json
{
	"quizId": "6f4f9a2a-9f61-4303-a7fa-6ef8b0d4e9f2",
	"questionIndex": 0,
	"optionIndex": 2,
	"answer": "Optional free-text fallback"
}
```

Current behavior:

- Validates that the answer is for the next expected question.
- Rejects stale or out-of-order submissions.
- Supports either explicit `answer` text or `optionIndex` selection.
- Atomically updates quiz history and messages in Redis.
- Snapshots QA history to PostgreSQL on a best-effort basis.
- Schedules more background graph work once the answered count reaches the current baseline threshold.

### Poll Status

`GET ${API_PREFIX}/quiz/status/{quiz_id}?known_questions_count=0`

Response variants:

- `{"status":"processing","quizId":"..."}` while the agent is still working
- `{"status":"active","type":"question","data":{...}}` when a new question is ready
- `{"status":"finished","type":"result","data":{...}}` when the final result exists

Important implementation details:

- The endpoint compares `known_questions_count` with the current generated question count to avoid re-sending a question the client already knows about.
- The next question index is also constrained by the number of recorded answers.
- When a question is returned, the service updates `last_served_index` in Redis.

### Result Retrieval

`GET ${API_PREFIX}/result/{result_id}`

This returns a persisted shareable result for a completed quiz session. `result_id` is currently the session UUID.

Response shape:

```json
{
	"title": "The Strategist",
	"description": "A final profile description.",
	"imageUrl": null,
	"category": "Which fantasy class am I?",
	"createdAt": "2026-04-25 18:09:17.053210+00:00"
}
```

### Feedback Submission

`POST ${API_PREFIX}/feedback`

Request body:

```json
{
	"quizId": "6f4f9a2a-9f61-4303-a7fa-6ef8b0d4e9f2",
	"rating": "up",
	"text": "The result felt accurate.",
	"cf-turnstile-response": "token-from-client"
}
```

Current behavior:

- The endpoint verifies Turnstile from the raw request body.
- The Turnstile token is removed before Pydantic validation of the actual feedback payload.
- Ratings are mapped to `POSITIVE` or `NEGATIVE` sentiment in PostgreSQL.
- Returns `204 No Content` on success.

## Persistence Model

### Redis

Redis stores the working graph state used by polling endpoints and background execution.

- Session key format: `quiz_session:{session_id}`
- Default TTL: 3600 seconds
- Storage format: validated JSON serialized from `AgentGraphStateModel`
- Update strategy for answers: optimistic concurrency via `WATCH/MULTI/EXEC`

State includes:

- `synopsis`
- `generated_characters`
- `generated_questions`
- `quiz_history`
- `ready_for_questions`
- `baseline_count`
- `final_result`
- trace metadata and error fields

### PostgreSQL

The database is the durable source for completed session data, result retrieval, feedback, and question snapshots.

Current ORM tables:

- `session_history`: one row per quiz session, including category, synopsis, transcript, character snapshot, final result, feedback, and completion fields
- `characters`: canonical character profiles keyed by unique name
- `character_session_map`: many-to-many link between sessions and characters
- `session_questions`: persisted baseline and adaptive question snapshots

Notable implementation details:

- `session_history.synopsis_embedding` uses `pgvector.Vector(384)` but is nullable, so session persistence does not block on embeddings.
- Character rows are upserted by name.
- Baseline questions are written once the graph marks them ready.
- Adaptive questions and final results are persisted after background graph completion.

## Configuration

### Non-Secret Configuration Sources

The settings loader in `app/core/config.py` merges configuration in this order:

1. Azure App Configuration, if wired
2. Local YAML file, defaulting to `backend/appconfig.local.yaml` or `APP_CONFIG_LOCAL_PATH`
3. In-code defaults

Canonical category lookups in `app/agent/canonical_sets.py` use an additional layered source order:

1. Built-in reviewed catalog in `app/agent/canonical_catalog.py`
2. `quizzical.canonical_sets` from `appconfig.local.yaml`
3. `settings.canonical_sets` runtime overrides, when present

The `sets` and `aliases` sections merge by title/key, so later layers can replace a single canonical set or alias group without wiping the rest of the reviewed catalog.

### Secret Sources

Current effective secret sources are process environment and `.env` files.

The loader contains Azure Key Vault scaffolding, but the Key Vault path is currently short-circuited in code, so local environment variables remain the active source unless that implementation is restored.

### Important Environment Variables

The checked-in sample `.env.example` currently documents these key variables:

- `APP_ENVIRONMENT`
- `DATABASE_URL` or the composed `DATABASE__*` values
- `REDIS_URL` or the composed `REDIS__*` values
- `GEMINI_API_KEY` (primary LLM provider; LiteLLM model strings use the `gemini/...` prefix)
- `OPENAI_API_KEY` (optional fallback; only required if you switch any tool back to an OpenAI model)
- `FAL_KEY` (FAL.ai image generation; legacy aliases `FAL_AI_KEY` / `FAL_AI_API_KEY` are mirrored at startup. Without any of these the image pipeline silently no-ops.)
- `TURNSTILE_SECRET_KEY`
- `ENABLE_TURNSTILE`
- `ALLOWED_ORIGINS`
- `APP_CONFIG_LOCAL_PATH`
- `TRUSTED_HOSTS` (§15.2 — JSON array or CSV of allowed `Host` headers; defaults to `localhost,127.0.0.1` in `production`/`staging` and `*` everywhere else)
- `MAX_REQUEST_BODY_BYTES` (default `262144`)
- `MAX_REQUEST_BODY_BYTES` (default `262144`)
- `ADMIN_IMPORT_MAX_BODY_BYTES` (default `33554432` / 32 MiB — applies only to `POST /admin/precompute/import` to accommodate multi-MB signed archives)

### Production Hardening (§15)

The backend ships with a small set of always-on production-safety middlewares and validators, configurable under `settings.security`:

- **Rate limiting (§15.1)** — Redis token-bucket middleware (`app/security/rate_limit.py`). Per `(client_ip, route_prefix)` bucket; defaults to 30 capacity / 1 token per second. Health, docs, openapi, and `/` are allowlisted. Returns `429` with `Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining`. Fails open when Redis is unreachable.
- **Trusted host (§15.2)** — `TrustedHostMiddleware` enabled when `TRUSTED_HOSTS` is set or env is `production`/`staging`. Untrusted Host header → 400.
- **Input validation (§15.3)** — `StartQuizRequest.category` rejects C0/C1 control characters, Unicode bidi-override codepoints (U+202A..U+202E, U+2066..U+2069), NUL bytes, and inputs whose UTF-8 byte length exceeds 400. Internal whitespace is normalized.
- **Single-flight session lock (§15.4)** — `/api/quiz/next` acquires a Redis `SET NX EX` lock keyed by `quiz_id`. Concurrent requests return `409 SESSION_BUSY`. Lock release is token-matched (Lua) so an expired lock is never deleted by the previous owner. Fails open on Redis errors.
- **PII log scrubbing (§15.5)** — Structlog processor masks email addresses (`local@***`), credit-card-like 13–19 digit runs (`****1234`), and JWT-shaped tokens (`eyJ***`) in any string value emitted by application loggers. Idempotent and recursive over nested dicts/lists.
- **Bounded ID lookups (§15.6)** — `CharacterRepository.get_many_by_ids` raises `ValueError` when given more than 100 IDs to prevent unbounded `WHERE id IN (...)` queries.
- **Image URL allowlist (§9.7.1)** — `ImageService.generate` returns `None` for any FAL response whose `images[].url` host is not in `image_gen.url_allowlist` (default `fal.media`, `v2.fal.media`, `v3.fal.media`) or that uses a non-`https` scheme. Defends against compromised/redirected providers.
- **Per-quiz feedback throttle (§9.7.4)** — `POST /api/feedback` is rate-limited per `quiz_id` via Redis token bucket (default 3/min). Same user can rate many quizzes; cannot spam a single one. Returns `429` with `Retry-After`. Fails open on Redis errors. Tunable under `security.feedback_rate_limit`.
- **LLM response size cap (§9.7.6)** — `LLMService.get_structured_response` raises `LLMResponseTooLargeError` and emits `llm.response.too_large` when the JSON-serialised provider response exceeds `llm.max_response_bytes` (default 256 KiB). Defends against memory-exhaustion from a buggy/compromised provider.

- **LLM response size cap (§9.7.6)** — `LLMService.get_structured_response` raises `LLMResponseTooLargeError` and emits `llm.response.too_large` when the JSON-serialised provider response exceeds `llm.max_response_bytes` (default 256 KiB). Defends against memory-exhaustion from a buggy/compromised provider.

### Scalability Hardening (§17)

A dedicated layer of guardrails keeps the service stable under load and during teardown:

- **LLM concurrency semaphore (§17.1)** — `app/services/llm_concurrency.py` exposes a process-wide `LLMConcurrencyLimiter` that `LLMService.get_structured_response` acquires for every call. Defaults: `llm.max_concurrency=16`, `llm.acquire_timeout_s=30.0`. Acquire timeouts raise `LLMConcurrencyTimeoutError` so a slow upstream cannot exhaust threads or memory. Live counters are exposed via `limiter.metrics()` for tests and observability.
- **Graceful shutdown drain (§17.2)** — On lifespan exit the app polls `LLMConcurrencyLimiter.metrics()["in_flight"]` every 50 ms for up to `shutdown_grace_s` (default `15.0` s) before disposing of the agent graph, DB engine, and Redis pool. Setting `shutdown_grace_s=0` disables the drain; if work remains when the grace window expires the lifespan logs `shutdown.in_flight_remaining` at warning and proceeds.
- **Session retention helper (§17.3)** — `SessionRepository.purge_older_than(days=N)` deletes `session_history` rows whose `last_updated_at` is older than `N` days, returning the row count. Rejects `days < 1` with `ValueError` so a misconfigured cron cannot wipe the table. Linkage rows are removed via the cascading FK on `character_session_map`.
- **Server-Timing per-segment breakdown (§17.4)** — Every API response carries a W3C `Server-Timing` header. The `app;dur=<ms>` baseline segment is always emitted; handlers can call `get_request_timing(request).record("db", elapsed_ms)` to attribute additional slices. Segment names are validated `[A-Za-z0-9][A-Za-z0-9_-]{0,63}` so a bad recorder call cannot inject CRLF or extra header fields. The header is in the CORS `expose_headers` list so the FE can read it client-side.

### Image Generation (FAL)

Synopsis, character, and final-result images are generated asynchronously via FAL.ai (`fal-ai/flux/schnell`) and persisted into the existing JSONB columns and the new `characters.image_url` column. Generation is fully non-blocking — `/api/quiz/start` schedules synopsis + character jobs via FastAPI `BackgroundTasks` and returns immediately, and the result image is generated inside the same background task that persists the final result. Every FAL call is wrapped in `asyncio.wait_for` with a hard timeout (default 15s) and a process-wide semaphore (default concurrency 4); failures, timeouts, and empty responses always return `None` and never propagate to the user request.

Because the background tasks persist URLs **only to Postgres** (never to the Redis-backed agent state polled by `/quiz/status`), the FE surfaces images via a separate read-only snapshot endpoint, `GET ${API_PREFIX}/quiz/{quiz_id}/media`. The FE polls it while the user is on the synopsis screen and merges any URLs it finds back into the already-rendered synopsis/character cards. The endpoint is fail-soft: missing rows, empty JSONB columns, and DB exceptions all return an empty snapshot with HTTP 200 so the page never depends on image generation having completed (AC-MEDIA-1..6).

Prompts are intentionally **descriptive, not nominal**: when the agent classifies the topic as a media franchise, character names and the franchise name are stripped from the prompt and replaced with their physical/personality descriptors plus a shared style suffix to keep the look consistent and IP-safe. Tunables live under `image_gen` in `appconfig.local.yaml` (`enabled`, `model`, `image_size`, `num_inference_steps`, `timeout_s`, `concurrency`, `style_suffix`, `negative_prompt`).

The synopsis and final-result hero images are generated at **1024×576 (16:9 landscape)** to match the landscape display containers on the synopsis and results pages, avoiding top/bottom cropping. Character portraits continue to use the configured default (`image_gen.image_size`, square 512×512).

### Frontend Config Endpoint

`GET ${API_PREFIX}/config` is slightly special:

- It reads YAML directly via `APP_CONFIG_PATH`, defaulting to `appconfig.local.yaml`
- It does not use the shared `settings` object for that response
- It mirrors both `features.turnstile` and `features.turnstileEnabled`
- It allows environment overrides for `ENABLE_TURNSTILE` and `TURNSTILE_SITE_KEY`

#### Static Page Content (Markdown)

The `quizzical.frontend.content` section of `appconfig.local.yaml` drives all static info pages
(About, Terms, Privacy, Donate). Each page key accepts:

| Field | Purpose |
| --- | --- |
| `title` | Page `<h1>` heading |
| `description` | Optional short description |
| `body` | Full page body as a **Markdown** string (preferred; rendered client-side via `react-markdown`) |
| `blocks` | Legacy structured blocks (`p`, `h2`, `ul`, `ol`, `markdown`) |

To update page content, edit `appconfig.local.yaml` and save — no server restart required in
development (the YAML is re-read per request). In production, re-deploying the config file or
restarting the service picks up changes within `CONFIG_CACHE_SECONDS` (default 60 s).

## Security and Operational Behavior

- CORS is enabled and defaults to localhost origins when `ALLOWED_ORIGINS` is not set.
- Turnstile verification is enforced when `settings.ENABLE_TURNSTILE` is true.
- In local or development environments, Turnstile is bypassed when disabled or effectively unconfigured.
- Every request gets an `X-Trace-ID` response header.
- When OpenTelemetry is available, the app also emits `traceparent` and `traceparent-id` headers.
- Unhandled exceptions are normalized into a `500` response with a stable error payload containing a trace ID.

## Local Development

### Prerequisites

- Python 3.11
- PostgreSQL
- Redis
- Poetry

### Install Dependencies

From the `backend/` directory:

```bash
poetry install --with dev
```

### Run the API

```bash
poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Docker

The backend Docker image is a two-stage Python 3.11 build.

- Builder stage installs Poetry and main dependencies.
- Final stage runs as a non-root user.
- Default container command is `uvicorn app.main:app --host 0.0.0.0 --port 8000`.

## Testing and Quality Checks

Common commands from the `backend/` directory:

```bash
poetry run pytest
poetry run pytest tests/smoke/test_quiz_smoke.py -q
poetry run ruff check .
poetry run black --check .
```

The repository currently includes smoke, integration, and unit coverage for the quiz flow, repositories, API dependencies, and agent behavior.

## Directory Map

| Path | Purpose |
| --- | --- |
| `app/main.py` | FastAPI app setup, middleware, lifespan, router registration |
| `app/api/endpoints/` | Public HTTP handlers |
| `app/api/dependencies.py` | DB, Redis, and Turnstile dependencies |
| `app/agent/` | LangGraph orchestration and tool wiring |
| `app/models/` | Pydantic and SQLAlchemy models |
| `app/services/` | Repositories, cache service, and LLM helpers |
| `tests/` | Unit, integration, and smoke tests |

## Pre-Computed Topic Knowledge Packs — Operator Runbook (§21)

The Phase-1–10 build of the precompute pipeline ships disabled
(`precompute.enabled=False`); `/quiz/start` is byte-for-byte identical
to the pre-§21 behaviour until enabled (Universal-G5).

### Operator authentication

All `/admin/precompute/*` and `/healthz/precompute` routes require
`Authorization: Bearer $OPERATOR_TOKEN` (≥ 32 bytes). In `production`,
the additional header `X-Operator-2FA: <one-time-code>` is enforced.

### Promote / rollback a pack

- **Enqueue a build job**:
  `POST /admin/precompute/jobs` with `{"topic_id": "<uuid>"}` → `201`
  with the new job row. Banned topics return `409 TOPIC_BANNED`.
- **Promote a built pack**:
  `POST /admin/precompute/promote` with `{"topic_id", "pack_id"}` —
  atomically sets `topics.current_pack_id` and writes an `audit_log`
  row (`AC-PRECOMP-PROMOTE-1`).
- **Rollback to a previous pack**:
  `POST /admin/precompute/rollback` with `{"topic_id", "to_pack_id"}` —
  reverses the pointer; both endpoints are idempotent.

### Quarantine and cascade

A pack can be quarantined manually or automatically:
- Manual: `POST /admin/precompute/jobs` is the wrong call here — use
  `app.services.precompute.quarantine.quarantine_pack(session, pack_id, reason)`
  in a one-shot script. Idempotent.
- Automatic: when a content flag's `target_id` accumulates more than
  `flagging.quarantine_threshold` (default `5`) distinct
  `target_kind="topic_pack"` flags within 24 h, the affected pack is
  quarantined and `topics.current_pack_id` is cleared
  (`AC-PRECOMP-FLAG-3` / `-4`).
- A flag against a `character` triggers
  `cascade_quarantine_for_character` over every `character_set`
  whose `composition.character_ids` includes that character
  (`AC-PRECOMP-FLAG-5`).

### Forget a user (GDPR)

`POST /admin/precompute/users/forget` with
`{"user_id": "<external_id>"}` removes the user's behavioural rows and
scrubs the user's `content_flags.reason_text` to `"[REDACTED]"` while
keeping the aggregate counters intact.

### Drift probe

The weekly evaluator drift probe lives at
`app/jobs/evaluator_drift_probe.py::run_drift_probe`. It re-judges a
sample of recent artefacts and returns a `DriftReport`. When
`drift_pp > pause_threshold_pp` (default 10 percentage points) the
caller MUST set `evaluator_drift_paused=True` and page on-call
(`AC-PRECOMP-QUAL-4`).

### Cost check

`GET /admin/precompute/cost` returns:
- `spent_cents`, `daily_cap_cents`, `tier3_cap_cents`, `remaining_cents`
- `topics_30d`: per-topic spend over the trailing 30 days, sorted
  desc — drives the "where is the budget going?" decision
  (`AC-PRECOMP-COST-4`).

### Health and seeding telemetry

`GET /healthz/precompute` (operator-gated) returns
`{packs_published, hits_24h, misses_24h, hit_rate_24h,
miss_rate_24h, top_misses_24h}`. Use `top_misses_24h` to pick the
next batch of topics to enqueue (`AC-PRECOMP-OBJ-3`).

### Starter seeding (cold-start)

The library entry point `scripts/import_packs.py::import_archive(session,
archive_payload, signature, secret)` imports a signed JSON archive of
starter packs. Refuses unsigned archives (`AC-PRECOMP-SEC-5`); skips
entirely when the destination DB already has at least one published
pack (`AC-PRECOMP-OBJ-2` / `AC-PRECOMP-MIGR-6`). Idempotent on
`content_hash` / `composition_hash`. Each imported topic also receives
`current_pack_id` and idempotent `topic_aliases` rows so alias / slug
lookup HITs resolve immediately (`AC-PRECOMP-MIGR-6a`).

The HTTP entry point is `POST /api/v1/admin/precompute/import`
(`AC-PRECOMP-SEC-5a`). Body: raw archive bytes
(`Content-Type: application/octet-stream`). Headers:
`Authorization: Bearer $OPERATOR_TOKEN`,
`X-Archive-Signature: <hex HMAC-SHA256>`. Returns `200`
`{packs_inserted, packs_skipped, skipped_db_not_empty}` and writes
one `audit_log` row.

Optional query parameter `force_upgrade=true` bypasses the global
"only seed when DB is empty" gate so an already-seeded environment can
ingest a new archive version. Per-pack idempotency on
`(topic_id, version)` still prevents duplicate inserts; rows of the
same version are skipped (`AC-PRECOMP-IMPORT-1`).

To build a signed archive locally, hand-author a source JSON of
topics (see `configs/precompute/starter_packs/starter_v1.source.json`
for synopsis-only v1 or `starter_v2.source.json` for v2 with inline
characters) and run:

```bash
PRECOMPUTE_HMAC_SECRET=<32+ bytes> python scripts/build_starter_packs.py \
  --source configs/precompute/starter_packs/starter_v2.source.json \
  --out    configs/precompute/starter_packs/starter_v2.json
```

The script emits the archive plus a sibling `*.sig` file containing the
detached HMAC signature.

#### Ranked candidate generation (operator draft flow)

When the precompute worker path is not yet wired for automatic build
consumption, operators can still start the next batch by generating a
reviewable v3 source document directly from the repo's existing agent
tools:

```bash
python -m scripts.generate_ranked_pack_candidates \
	--limit 5 \
	--budget-usd 50 \
	--estimated-usd-per-topic 0.05 \
	--topic-pool configs/precompute/starter_packs/llm_topic_pool.json \
	--judge \
	--judge-pass-score 75 \
	--spend-cap-usd 50 \
	--out configs/precompute/starter_packs/starter_ranked_candidates_top5.source.json \
	--report-out configs/precompute/starter_packs/starter_ranked_candidates_top5.report.json
```

Queue selection is deterministic:
- unpacked production `topics` rows with the smallest `popularity_rank`
	come first when a `--database-url` value is supplied;
- otherwise the script falls back to a curated evergreen ranking list
	checked into the repo.

The script emits:
- a v3-compatible source document that can be fed straight into
	`scripts/build_starter_packs.py` after operator review;
- a machine-readable evaluation report that flags structural failures
	(empty synopsis, duplicate character names, duplicate question text,
	question/option count drift, etc.).

Transient partial drafts are retried up to three times per topic. The
script stops early on the first structurally valid topic; otherwise it
keeps the best attempt and still marks that topic as not ready in the
report.

The current v3 pack contract remains fixed at **4–6 characters** and
**exactly 5 baseline questions with 4 options each**, regardless of the
runtime quiz config (`AC-PRECOMP-DRAFT-1`..`AC-PRECOMP-DRAFT-5`).

#### Offline image generation + quality gate (operator flow)

After a draft run, operators can generate images for judge-passed topics
and enforce a quality gate (relevancy, correctness, style adherence):

```bash
python -m scripts.generate_images_for_packs \
	--source configs/precompute/starter_packs/starter_ranked_candidates_top250.source.json \
	--report configs/precompute/starter_packs/starter_ranked_candidates_top250.report.json \
	--out configs/precompute/starter_packs/starter_ranked_candidates_top250.source.json \
	--spend-cap-usd 20
```

To evaluate already-generated image URLs (without re-rendering), run:

```bash
python -m scripts.generate_images_for_packs \
	--source configs/precompute/starter_packs/starter_ranked_candidates_top250.source.json \
	--report configs/precompute/starter_packs/starter_ranked_candidates_top250.report.json \
	--out configs/precompute/starter_packs/starter_ranked_candidates_top250.source.json \
	--spend-cap-usd 10 \
	--evaluate-existing
```

In `--evaluate-existing` mode, failed image evaluations are cleared
(`image_url = null`) so only gated assets remain in the exported source.

#### Pack archive `version=2` (with inline characters)

A v2 source entry adds a `characters` array of
`{name, short_description, profile_text}` objects alongside the
synopsis. The build script computes a deterministic
`character_keys` list (canonical names) and embeds the inline
character bodies in the pack entry. On import,
`scripts/import_packs.py` upserts each character row by canonical
name and rewrites the persisted `character_set.composition` to
`{"character_ids": [<uuid>, …]}` so the read-path hydrator can
resolve the cards (`AC-PRECOMP-IMPORT-2`).

#### Pack archive `version=3` (with inline baseline questions)

A v3 source entry adds a `baseline_questions` array of
`{question_text, options: [{text}, ...]}` objects per topic
(typically 5 questions × 4 options). The build script hashes each
question's `{text, options}` payload (`q-<sha256-hex>`), embeds the
full inline `questions` array in the pack entry, and stores
`baseline_question_set.composition.question_keys` as a list of those
text hashes. On import, `scripts/import_packs.py` upserts each
`questions` row by `text_hash` (kind=`baseline`) and rewrites the
persisted composition to `{"question_ids": [<uuid>, …]}`
(`AC-PRECOMP-IMPORT-3`). Re-importing the same archive is a no-op.

A v3 entry's character objects may also carry an optional `image_url`
field. On first creation the importer writes that URL to
`characters.image_url`; for pre-existing rows the URL is backfilled
only when `image_url IS NULL` so curated values are never
overwritten (`AC-PRECOMP-IMPORT-4`).

### `/quiz/start` short-circuit (Phase 3+)

When `precompute.enabled=True` and the resolver returns a HIT for the
incoming topic, `/quiz/start` skips the LangGraph agent entirely and
serves the pre-baked synopsis + character cards straight from the
hydrated pack. From v3 onward the hydrator also surfaces pre-baked
baseline questions, which are stored in the session's GraphState
(`generated_questions`, `baseline_ready=True`, `baseline_count`) so
the subsequent `/quiz/proceed` can short-circuit too. The endpoint
still schedules image generation in the background via the existing
FAL pipeline so character icons appear within the normal latency
budget. Telemetry: a `precompute.start.short_circuit` structlog event
is emitted on success; `precompute.start.short_circuit.skip_no_content`
is emitted when the resolved pack is synopsis-only (the request falls
through to the live agent path); any unexpected exception is caught
and logged as `precompute.start.short_circuit_error` and the request
continues through the live agent path so users always get an
experience (`AC-PRECOMP-HIT-1`..`AC-PRECOMP-HIT-5`).

### `/quiz/proceed` short-circuit (Phase 4)

When the session was opened via the precompute path
(`agent_plan.source='precompute'`) and the v3+ pack populated state
with pre-baked baseline questions (`baseline_ready=True`, non-empty
`generated_questions`), the first `/quiz/proceed` call schedules
`_persist_baseline_questions` directly and returns `202` without
invoking the LangGraph agent. A `precompute.proceed.short_circuit`
structlog event fires with `quiz_id`, `pack_id`, and `baseline_count`.
Sessions whose state lacks the precompute provenance are unaffected:
they continue to schedule `run_agent_in_background` exactly as
before. Failures inside the short-circuit are caught
(`precompute.proceed.short_circuit_error`) and the request falls
through to the live agent path so users always get questions
(`AC-PRECOMP-PROCEED-1`..`AC-PRECOMP-PROCEED-3`).

### Local → Azure Blob migration

After the 7-day dual-write window closes
(`AC-PRECOMP-MIGR-1`), run the one-shot migrator
`scripts/migrate_local_to_blob.py::migrate_local_to_blob(session,
provider=AzureBlobProvider.from_settings())` to drain
`media_assets.bytes_blob` into the configured Azure Blob container and
flip `storage_provider` to `'blob'` (`AC-PRECOMP-MIGR-2`). Rows
without source bytes are marked `pending_rehost=true` so an async
worker can re-derive them later. The migrator is idempotent —
re-running over an already-migrated table is a no-op. Drop the
`bytes_blob` column only after a successful run is verified.

## Notes for Contributors

- Keep this README aligned with the actual backend contract. If routes, payloads, storage, configuration behavior, or operational expectations change, update this file in the same change.
- Prefer documenting current behavior explicitly over aspirational architecture.
- Avoid placeholder setup instructions in public docs; this README should remain runnable and accurate.
