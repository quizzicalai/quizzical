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
| `GET` | `${API_PREFIX}/result/{result_id}` | Returns a persisted shareable result for a completed session |
| `POST` | `${API_PREFIX}/feedback` | Stores thumbs-up or thumbs-down feedback with an optional comment |

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

### Production Hardening (§15)

The backend ships with a small set of always-on production-safety middlewares and validators, configurable under `settings.security`:

- **Rate limiting (§15.1)** — Redis token-bucket middleware (`app/security/rate_limit.py`). Per `(client_ip, route_prefix)` bucket; defaults to 30 capacity / 1 token per second. Health, docs, openapi, and `/` are allowlisted. Returns `429` with `Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining`. Fails open when Redis is unreachable.
- **Trusted host (§15.2)** — `TrustedHostMiddleware` enabled when `TRUSTED_HOSTS` is set or env is `production`/`staging`. Untrusted Host header → 400.
- **Input validation (§15.3)** — `StartQuizRequest.category` rejects C0/C1 control characters, Unicode bidi-override codepoints (U+202A..U+202E, U+2066..U+2069), NUL bytes, and inputs whose UTF-8 byte length exceeds 400. Internal whitespace is normalized.
- **Single-flight session lock (§15.4)** — `/api/quiz/next` acquires a Redis `SET NX EX` lock keyed by `quiz_id`. Concurrent requests return `409 SESSION_BUSY`. Lock release is token-matched (Lua) so an expired lock is never deleted by the previous owner. Fails open on Redis errors.
- **PII log scrubbing (§15.5)** — Structlog processor masks email addresses (`local@***`), credit-card-like 13–19 digit runs (`****1234`), and JWT-shaped tokens (`eyJ***`) in any string value emitted by application loggers. Idempotent and recursive over nested dicts/lists.
- **Bounded ID lookups (§15.6)** — `CharacterRepository.get_many_by_ids` raises `ValueError` when given more than 100 IDs to prevent unbounded `WHERE id IN (...)` queries.

### Image Generation (FAL)

Synopsis, character, and final-result images are generated asynchronously via FAL.ai (`fal-ai/flux/schnell`) and persisted into the existing JSONB columns and the new `characters.image_url` column. Generation is fully non-blocking — `/api/quiz/start` schedules synopsis + character jobs via FastAPI `BackgroundTasks` and returns immediately, and the result image is generated inside the same background task that persists the final result. Every FAL call is wrapped in `asyncio.wait_for` with a hard timeout (default 15s) and a process-wide semaphore (default concurrency 4); failures, timeouts, and empty responses always return `None` and never propagate to the user request.

Prompts are intentionally **descriptive, not nominal**: when the agent classifies the topic as a media franchise, character names and the franchise name are stripped from the prompt and replaced with their physical/personality descriptors plus a shared style suffix to keep the look consistent and IP-safe. Tunables live under `image_gen` in `appconfig.local.yaml` (`enabled`, `model`, `image_size`, `num_inference_steps`, `timeout_s`, `concurrency`, `style_suffix`, `negative_prompt`).

### Frontend Config Endpoint

`GET ${API_PREFIX}/config` is slightly special:

- It reads YAML directly via `APP_CONFIG_PATH`, defaulting to `appconfig.local.yaml`
- It does not use the shared `settings` object for that response
- It mirrors both `features.turnstile` and `features.turnstileEnabled`
- It allows environment overrides for `ENABLE_TURNSTILE` and `TURNSTILE_SITE_KEY`

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

## Notes for Contributors

- Keep this README aligned with the actual backend contract. If routes, payloads, storage, configuration behavior, or operational expectations change, update this file in the same change.
- Prefer documenting current behavior explicitly over aspirational architecture.
- Avoid placeholder setup instructions in public docs; this README should remain runnable and accurate.
