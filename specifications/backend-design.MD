# Quizzical — Backend Design Specification

> **Status:** Living document. This file MUST be updated BEFORE any change is made to `backend/`.
> All acceptance criteria defined here drive tests (TDD). Spec changes precede code changes.
>
> Last reviewed: 2026-04-26 | Matches implementation commit post-cleanup

---

## Table of Contents

1. [Overview & Guiding Principles](#1-overview--guiding-principles)
2. [Architecture](#2-architecture)
3. [Configuration System](#3-configuration-system)
4. [API Design](#4-api-design)
   - 4.1 Health & Readiness
   - 4.2 Config
   - 4.3 Quiz Lifecycle (start / proceed / next / status)
   - 4.4 Results
   - 4.5 Feedback
5. [Data Structures & Schemas](#5-data-structures--schemas)
   - 5.1 API Request/Response Schemas (Pydantic)
   - 5.2 Internal Agent State Schema
   - 5.3 Database ORM Models
   - 5.4 Redis Payloads
6. [Persistence Processes](#6-persistence-processes)
   - 6.1 PostgreSQL Operations
   - 6.2 Redis Operations
   - 6.3 Session Lifecycle State Machine
7. [Agent System (LangGraph)](#7-agent-system-langgraph)
   - 7.1 Graph Topology
   - 7.2 Node Specifications
   - 7.3 Routing Logic
   - 7.4 Tool Inventory
   - 7.5 LLM Tool Models
   - 7.6 Prompts
8. [Services Layer](#8-services-layer)
9. [Security](#9-security)
   - 9.1 Cloudflare Turnstile
   - 9.2 CORS
   - 9.3 Security Headers
   - 9.4 Request Body Size Limit
   - 9.5 OpenAPI Docs Gating
   - 9.6 Log Redaction
10. [Observability](#10-observability)
11. [Error Handling & HTTP Status Codes](#11-error-handling--http-status-codes)
12. [Environment Variables Reference](#12-environment-variables-reference)
13. [Dependency Stack](#13-dependency-stack)
14. [Acceptance Criteria & Test Matrix](#14-acceptance-criteria--test-matrix)
    - 14.1 Functional Acceptance Criteria
    - 14.2 Security Acceptance Criteria
    - 14.3 Performance Acceptance Criteria
    - 14.4 Edge & Resilience Criteria
    - 14.5 Test File Map

---

## 1. Overview & Guiding Principles

**Product purpose:** Quizzical generates personalised "Which character are you?" quizzes for any user-supplied topic. A quiz produces a final character/archetype result with title, description, and optional image URL.

**Backend role:** FastAPI ASGI server + async LangGraph agent. Responsible for:
- Accepting quiz topics, orchestrating AI generation, serving questions, delivering results
- Persisting all session data (PostgreSQL), transient state (Redis), and agent checkpoints (Redis via LangGraph)
- Enforcing security, rate integrity, and structured observability

**Guiding principles:**
- Async throughout; no blocking I/O on the event loop
- Fail-safe for security; fail-open only for non-critical cache reads (Redis misses → proceed safely)
- Pydantic v2 for all IO boundaries
- Idempotent DB writes where possible (`ON CONFLICT DO UPDATE`)
- Short, typed functions; extract helpers for repeated patterns
- No wildcard imports; snake_case vars/functions, PascalCase classes, UPPER_SNAKE constants
- OWASP ASVS hygiene; secrets never in logs

---

## 2. Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                           FastAPI App (ASGI)                           │
│  main.py — lifespan, middleware stack, health/readiness routes         │
│                                                                         │
│  Middleware (applied top-down on request, bottom-up on response):      │
│  1. TrustedHostMiddleware                                               │
│  2. MaxBodySizeMiddleware (custom)                                     │
│  3. SecurityHeadersMiddleware (custom)                                  │
│  4. CORSMiddleware (starlette)                                          │
│                                                                         │
│  Routers mounted under ${api_prefix}:                                   │
│  ├── /config          (config.py)                                       │
│  ├── /quiz/*          (quiz.py)                                         │
│  ├── /result/{id}     (results.py)                                      │
│  └── /feedback        (feedback.py)                                     │
└────────────────────────────────────────────────────────────────────────┘
             │                          │
    ┌────────┴────────┐       ┌─────────┴──────────┐
    │   PostgreSQL     │       │       Redis          │
    │  (asyncpg +      │       │  (redis[hiredis])    │
    │   SQLAlchemy)    │       │  - quiz session state│
    │                  │       │  - RAG cache         │
    │  Tables:         │       │  - LG checkpoints    │
    │  - characters    │       └────────────────────── ┘
    │  - session_history│
    │  - session_questions│
    │  - character_session_map│
    └──────────────────┘

             │
    ┌────────┴──────────────────────────────────────────┐
    │          LangGraph Agent (app/agent/graph.py)       │
    │                                                      │
    │  Nodes: bootstrap → generate_characters →           │
    │         [phase_router] → generate_baseline_questions│
    │         → decide_or_finish → generate_adaptive_     │
    │           question → assemble_and_finish             │
    │                                                      │
    │  Tools: planning_tools, content_creation_tools,     │
    │         data_tools, intent_classification            │
    │                                                      │
    │  LLM via LiteLLM (Gemini/OpenAI)                    │
    └──────────────────────────────────────────────────────┘
```

### Startup Sequence (lifespan)

1. Load `Settings` (lru_cache singleton): YAML → env override → secrets
2. Configure structlog + stdlib logging handlers
3. Create SQLAlchemy async engine + connection pool; run `pre_ping` check
4. Create Redis pool (`BlockingConnectionPool`, 50 max, 10s wait, 5s connect, 5s cmd timeout)
5. Compile LangGraph: attempt `AsyncRedisSaver` → fall back to `InMemorySaver`
6. Store `engine`, `redis_pool`, `agent_graph` on `app.state`
7. On shutdown: close engine, close Redis pool

### Module Layout

```
backend/
├── app/
│   ├── main.py                  # App factory, lifespan, middleware, health routes
│   ├── agent/
│   │   ├── _settings_proxy.py   # Thin proxy for injecting settings in tests
│   │   ├── canonical_catalog.py # Built-in reviewed canonical-set catalog (10 themed passes)
│   │   ├── canonical_sets.py    # Layered canonical-set loader (built-in catalog + config overrides)
│   │   ├── graph.py             # LangGraph StateGraph, all nodes, routing, compile
│   │   ├── llm_helpers.py       # invoke_structured() wrapper over LLMService
│   │   ├── prompts.py           # PromptManager + all prompt templates
│   │   ├── schemas.py           # StrictBase, AgentGraphStateModel, JSON schema builders
│   │   ├── state.py             # GraphState TypedDict + re-exports
│   │   └── tools/
│   │       ├── content_creation_tools.py
│   │       ├── data_tools.py
│   │       ├── intent_classification.py
│   │       └── planning_tools.py
│   ├── api/
│   │   ├── dependencies.py      # DB/Redis pools, Turnstile verify, FastAPI Depends
│   │   └── endpoints/
│   │       ├── config.py
│   │       ├── feedback.py
│   │       ├── quiz.py
│   │       └── results.py
│   ├── core/
│   │   ├── config.py            # Pydantic Settings, YAML loader, get_settings()
│   │   └── logging_config.py    # structlog config, redaction, sampling
│   ├── models/
│   │   ├── api.py               # All Pydantic API request/response schemas
│   │   └── db.py                # SQLAlchemy ORM models
│   └── services/
│       ├── database.py          # CharacterRepository, SessionRepository, etc.
│       ├── llm_service.py       # LiteLLM Responses API wrapper
│       └── redis_cache.py       # CacheRepository
├── db/init/init.sql             # Idempotent schema (tables, indexes, triggers, pgvector)
├── tests/                       # All test suites (see §14)
├── appconfig.local.yaml         # Non-secret local/dev config override
├── pyproject.toml               # Poetry project + all deps
└── Dockerfile
```

---

## 3. Configuration System

### Load Order

Non-secrets (merged in order, last wins):
1. Hardcoded `_DEFAULTS` dict in `app/core/config.py`
2. Local YAML at path from `APP_CONFIG_LOCAL_PATH` env var (default `backend/appconfig.local.yaml`)
3. Azure App Configuration — **not yet wired; stub returns nothing**

Secrets (merged in order, last wins):
1. Azure Key Vault — **disabled; stub returns `None` immediately**
2. Process environment / `.env` file

Deep-merge: dict values are merged recursively; scalars overwrite.

`get_settings()` is decorated with `@lru_cache` — singleton for the process lifetime. Tests can clear the cache via `get_settings.cache_clear()` or use the `_settings_proxy.py` injection pattern.

### Settings Model Hierarchy

```
Settings (Pydantic BaseSettings)
├── app: AppInfo
│   ├── name: str = "Quizzical"
│   ├── environment: str = "local"          # from APP_ENVIRONMENT env var
│   └── debug: bool = False
│
├── feature_flags: FeatureFlags
│   └── flow_mode: str = "agent"            # "agent" | "local"
│
├── cors: CorsConfig
│   └── origins: List[str]                  # from ALLOWED_ORIGINS env var
│
├── project: ProjectConfig
│   └── api_prefix: str = "/api"            # override in YAML for /api/v1
│
├── retrieval: RetrievalSettings
│   ├── policy: Literal["off","media_only","auto"] = "media_only"
│   ├── allow_wikipedia: bool = False
│   ├── allow_web: bool = False
│   ├── max_calls_per_run: int = 0
│   └── allowed_domains: List[str] | None = None
│
├── quiz: QuizConfig
│   ├── min_characters: int = 4
│   ├── max_characters: int = 6
│   ├── baseline_questions_n: int = 5
│   ├── max_options_m: int = 4
│   ├── max_total_questions: int = 20
│   ├── min_questions_before_early_finish: int = 6
│   ├── early_finish_confidence: float = 0.9
│   ├── first_step_timeout_s: float = 30.0
│   ├── stream_budget_s: float = 30.0
│   └── character_concurrency: int | None = None  # defaults to min(4, len(archetypes)) at runtime; capped to ≤ 8 in production (AC-PERF-CHAR-2)
│   └── batch_max_archetypes: int = 6  # AC-PERF-CHAR-1 — skip batch profile call beyond this
│
├── agent: AgentConfig
│   └── max_retries: int = 3
│
├── llm: LLMGlobals
│   └── per_call_timeout_s: int = 30
│
├── llm_tools: Dict[str, ModelConfig]       # per-tool LLM model overrides
│   # ModelConfig fields:
│   #   model: str
│   #   temperature: float = 0.3
│   #   max_output_tokens: int = 1024
│   #   timeout_s: int = 20  (most tools); web_search=20, safety_checker=10
│   #   json_output: bool = True
│   #   effort: Literal["low","medium","high"] | None = None  (o-series)
│   #   tool_choice: Literal["auto"] | Dict[str,Any] = "auto"
│   #   allowed_domains: List[str] | None = None  (web_search)
│   #   user_location: WebUserLocation | None = None  (web_search)
│   #   include_sources: bool = True  (web_search)
│
├── llm_prompts: Dict[str, PromptConfig]    # per-tool prompt overrides
│
└── security: SecurityConfig
    ├── enabled: bool                        # from ENABLE_TURNSTILE env var
    └── turnstile: TurnstileConfig
        ├── site_key: str                    # from TURNSTILE_SITE_KEY env var
        └── secret_key: str                  # from TURNSTILE_SECRET_KEY env var
```

### `appconfig.local.yaml` (key sections)

```yaml
project:
  api_prefix: "/api/v1"

quiz:
  min_characters: 2
  max_characters: 32
  baseline_questions_n: 6
  max_options_m: 6
  max_total_questions: 15
  min_questions_before_early_finish: 8
  early_finish_confidence: 0.85
  first_step_timeout_s: 90.0
  stream_budget_s: 30.0

llm_tools:
  plan_quiz:
    model: "gemini/gemini-2.5-flash"
    temperature: 0.3
  generate_character_list:
    model: "gemini/gemini-2.5-flash"
  draft_character_profiles:
    model: "gemini/gemini-2.5-flash"
  # ... (all tools use gemini/gemini-2.5-flash locally)

retrieval:
  policy: "off"
```

Note: The `QuizConfig` values in `app/core/config.py` are hardcoded framework defaults, but this repository's `appconfig.local.yaml` intentionally overrides several quiz knobs for local/dev behavior.

---

## 4. API Design

### Base URL

`${API_BASE_URL}${api_prefix}` — e.g., `https://api.example.com/api/v1`

All routes are prefixed with `settings.project.api_prefix`. The prefix is configurable; all tests and clients MUST read this dynamically from settings or the `/config` endpoint.

---

### 4.1 Health & Readiness

#### `GET /health` — Liveness probe

- **Auth:** None
- **Purpose:** Signals the process is alive (no dependency checks)
- **Response `200`:**
  ```json
  { "status": "ok" }
  ```
- **Notes:** Never returns non-200 while the process is running.

#### `GET /readiness` — Readiness probe

- **Auth:** None
- **Purpose:** Confirms DB and Redis are reachable before accepting traffic
- **Timeout:** `READINESS_PROBE_TIMEOUT_S` (default `2.0s`) for each check
- **Response `200`:**
  ```json
  { "status": "ready" }
  ```
- **Response `503`:**
  ```json
  { "status": "unready", "reason": "db" | "redis" | "db_timeout" | "redis_timeout" }
  ```
- **Acceptance:**
  - AC-HEALTH-1: Returns `200 {"status":"ok"}` unconditionally while the process runs
  - AC-HEALTH-2: Returns `200 {"status":"ready"}` when DB and Redis are up
  - AC-HEALTH-3: Returns `503 {"status":"unready","reason":"db"}` when DB is down
  - AC-HEALTH-4: Returns `503 {"status":"unready","reason":"redis"}` when Redis is down
  - AC-HEALTH-5: Returns `503 {"status":"unready","reason":"db_timeout"}` when DB probe exceeds `READINESS_PROBE_TIMEOUT_S`
  - AC-HEALTH-6: Returns `503 {"status":"unready","reason":"redis_timeout"}` when Redis probe exceeds `READINESS_PROBE_TIMEOUT_S`

---

### 4.2 Config

#### `GET ${prefix}/config` — Frontend configuration

- **Auth:** None
- **Purpose:** Exposes frontend feature flags, Turnstile config, theme, and limits sourced from YAML
- **Cache:** `Cache-Control: public, max-age={CONFIG_CACHE_SECONDS}` (default 60)
- **Max YAML size:** `CONFIG_MAX_BYTES` (default 1 MiB); returns `500` if exceeded
- **Response `200`:**
  ```json
  {
    "theme": {
      "colors": { "primary": "R G B", "secondary": "R G B", "...": "..." },
      "fonts": { "sans": "Inter, sans-serif", "serif": "Nunito, sans-serif" },
      "fontSizes": { "body": "1rem", "landingTitle": "2.25rem", "...": "..." },
      "layout": { "landing": { "cardMaxW": "56rem", "...": "..." } }
    },
    "content": {
      "appName": "string",
      "landingPage": { "title": "string", "subtitle": "string", "...": "..." },
      "footer": { "about": { "label": "About", "href": "/about" }, "...": "..." },
      "errors": { "title": "string", "requestTimeout": "string", "...": "..." }
    },
    "limits": {
      "validation": { "category_min_length": 3, "category_max_length": 80 }
    },
    "apiTimeouts": {
      "default": 15000,
      "startQuiz": 60000,
      "poll": { "total": 60000, "interval": 1000, "maxInterval": 5000 }
    },
    "features": {
      "turnstile": true,
      "turnstileEnabled": true,
      "turnstileSiteKey": "0x..."
    }
  }
  ```
- **Acceptance:**
  - AC-CONFIG-1: Returns `200` with valid JSON containing all top-level keys
  - AC-CONFIG-2: Response includes `Cache-Control: public, max-age=60` header (or configured value)
  - AC-CONFIG-3: `features.turnstile` resolves with precedence: `ENABLE_TURNSTILE` env override → YAML `features.turnstile` → YAML `features.turnstileEnabled` → default `true`
  - AC-CONFIG-4: `features.turnstileSiteKey` matches `TURNSTILE_SITE_KEY` env var
  - AC-CONFIG-5: YAML override values appear in response

Note: `limits.validation.category_min_length` / `category_max_length` are currently frontend guidance values from config. Backend `POST /quiz/start` request validation is enforced by `StartQuizRequest` (`min_length=3`, `max_length=100`).

---

### 4.3 Quiz Lifecycle

#### `POST ${prefix}/quiz/start` — Begin a new quiz

- **Auth:** Cloudflare Turnstile (see §9.1)
- **Request body:** `StartQuizRequest`
  ```json
  {
    "category": "string, 3–100 chars, required",
    "cf-turnstile-response": "string, required"
  }
  ```
- **Response `201`:** `FrontendStartQuizResponse`
  ```json
  {
    "quizId": "UUID",
    "initialPayload": {
      "type": "synopsis",
      "data": {
        "type": "synopsis",
        "title": "string",
        "summary": "string"
      }
    },
    "charactersPayload": {
      "type": "characters",
      "data": [
        {
          "name": "string",
          "shortDescription": "string",
          "profileText": "string",
          "imageUrl": null
        }
      ]
    }
  }
  ```
- **Errors:**
  - `400`: Turnstile token missing or malformed
  - `401`: Turnstile token failed verification
  - `422`: `category` fails length/type validation
  - `503`: Agent failed to produce a synopsis (no LLM output)
  - `504`: Agent timed out (`first_step_timeout_s`)
  - `500`: Unhandled internal error
- **Side effects:**
  1. LangGraph `bootstrap` + `generate_characters` nodes execute synchronously within the timeout
  2. `session_history` row upserted with `session_id`, `category`, `category_synopsis`, `character_set`, `agent_plan`
  3. Characters upserted in `characters` table; associations written to `character_session_map`
  4. Full agent state saved to Redis under `quiz_session:{session_id}` (TTL 3600s)
- **Acceptance:**
  - AC-START-1: Valid topic + valid Turnstile → `201` with `quizId` (UUID) + synopsis + characters list
  - AC-START-2: `category` < 3 chars → `422`
  - AC-START-3: `category` > 100 chars → `422`
  - AC-START-4: Missing `cf-turnstile-response` → `400`
  - AC-START-5: Invalid Turnstile token → `401`
  - AC-START-6: Agent timeout → `504`
  - AC-START-7: `charactersPayload.data` length is within `[min_characters, max_characters]`
  - AC-START-8: Session row exists in DB after `201`
  - AC-START-9: State key exists in Redis after `201`
  - AC-START-10: Whitespace-only category → `422`
  - AC-START-11: Character profiles whose `profile_text` is empty/whitespace MUST be filtered out of `state["generated_characters"]` before the snapshot is persisted, before the response is built, and before image-generation background tasks are scheduled. This prevents a single failed-LLM character (empty `profile_text`) from violating the `characters_profile_text_check` CHECK constraint and rolling back the entire `session_history` insert (which would in turn cause image background-task `UPDATE`s to silently target zero rows, leaving `/quiz/{id}/media` permanently empty).
  - AC-START-12: When at least one character is filtered out per AC-START-11, a single `start_quiz.characters.filtered_empty` warning event is logged with `quiz_id`, `dropped_count`, and `dropped_names`.

---

#### `POST ${prefix}/quiz/proceed` — Mark user ready for questions

- **Auth:** None
- **Request body:** `ProceedRequest`
  ```json
  { "quizId": "UUID" }
  ```
- **Response `202`:** `ProcessingResponse`
  ```json
  { "status": "processing", "quizId": "UUID" }
  ```
- **Errors:**
  - `404`: `quizId` not found in DB or Redis
  - `422`: `quizId` not a valid UUID
- **Side effects:**
  1. Sets `ready_for_questions = True` in Redis state (atomic update)
  2. Fires background task: LangGraph continues from checkpoint, executes `generate_baseline_questions` node
- **Acceptance:**
  - AC-PROCEED-1: Valid `quizId` that exists → `202 {"status":"processing"}`
  - AC-PROCEED-2: Unknown `quizId` → `404`
  - AC-PROCEED-3: After `202`, polling `/quiz/status/{quizId}` eventually returns a question (not "processing") within `stream_budget_s`
  - AC-PROCEED-4: Malformed UUID → `422`

---

#### `POST ${prefix}/quiz/next` — Submit an answer and request the next question

- **Auth:** None
- **Request body:** `NextQuestionRequest`
  ```json
  {
    "quizId": "UUID",
    "questionIndex": 0,
    "answer": "optional free text, max 2048 chars",
    "optionIndex": 0
  }
  ```
  - At least one of `answer` (non-empty after strip) or `optionIndex` (int ≥ 0, ≤ 1000) is required
  - `questionIndex` validation is performed server-side against expected index in state
- **Response `202`:** `ProcessingResponse`
  ```json
  { "status": "processing", "quizId": "UUID" }
  ```
- **Errors:**
  - `400`: Neither `answer` (after strip) nor `optionIndex` provided; `answer` exceeds 2048 chars
  - `404`: `quizId` not found in Redis
  - `409`: `questionIndex` out-of-order (> expected index); or atomic Redis update `gave_up` after 8 retries
  - `422`: Schema validation failure (bad UUID, field types, etc.)
- **Internal `questionIndex` validation:**
  - `expected_index = len(quiz_history)` (number already recorded)
  - `request.question_index < expected_index` → duplicate/already-recorded; return `202` silently (idempotent)
  - `request.question_index > expected_index` → `409` out-of-order
  - `request.question_index == expected_index` → valid; proceed
- **Side effects:**
  1. Answer atomically appended to `quiz_history` in Redis via `update_quiz_state_atomically` (WATCH/MULTI/EXEC)
  2. Best-effort incremental snapshot: `qa_history` written to `session_history.qa_history` in DB (non-fatal)
  3. Background task triggered if `len(new_quiz_history) >= baseline_count`: LangGraph resumes, runs `decide_or_finish` → optionally `generate_adaptive_question`
- **Acceptance:**
  - AC-NEXT-1: Valid answer with correct `questionIndex` → `202`; state updated in Redis
  - AC-NEXT-2: `questionIndex` > expected → `409`
  - AC-NEXT-3: `questionIndex` < expected (duplicate) → `202` silently (no state change)
  - AC-NEXT-4: `answer` exceeding 2048 chars → `400` (Pydantic)
  - AC-NEXT-5: Both `answer` (empty/whitespace) and `optionIndex` absent → `400` (model validator)
  - AC-NEXT-6: Unknown `quizId` → `404`
  - AC-NEXT-7: After final answer, polling `/quiz/status/{quizId}` eventually returns `status: "finished"` with result
  - AC-NEXT-8: Atomic Redis update failing all 8 retries → `409`; state unchanged

---

#### `GET ${prefix}/quiz/status/{quiz_id}` — Poll quiz state

- **Auth:** None
- **Path param:** `quiz_id` (UUID)
- **Query param:** `known_questions_count` (int ≥ 0, default `0`) — the number of questions the client already has
- **Response `200`:** Discriminated union on `type`:
  - **Question available** (`type: "question"`, `status: "active"`):
    ```json
    {
      "status": "active",
      "type": "question",
      "data": {
        "text": "string",
        "imageUrl": null,
        "options": [{ "text": "string", "imageUrl": null }]
      }
    }
    ```
  - **Still processing** (`status: "processing"`):
    ```json
    { "status": "processing", "quizId": "UUID" }
    ```
  - **Finished** (`type: "result"`, `status: "finished"`):
    ```json
    {
      "status": "finished",
      "type": "result",
      "data": {
        "title": "string",
        "imageUrl": null,
        "description": "string"
      }
    }
    ```
- **Errors:**
  - `404`: `quiz_id` not found in Redis
  - `500`: Malformed result or question data in state
- **Internal logic:**
  - Reads state from Redis; `404` if missing
  - `final_result` present in state → attempt to return `QuizStatusResult` (`finished`); `500` if parse fails
  - `target_index = max(len(quiz_history), known_questions_count)` — clients use `known_questions_count` to request question N
  - If `len(generated_questions) > target_index` → serve `generated_questions[target_index]` as `QuizStatusQuestion`
  - Else → `ProcessingResponse` (more questions pending)
  - On serve: atomically update `last_served_index = target_index` (non-fatal on update failure)
- **Acceptance:**
  - AC-STATUS-1: Returns `404` for unknown quiz_id
  - AC-STATUS-2: Returns `processing` while questions are still being generated
  - AC-STATUS-3: Returns `question` at correct index once available; `data.options` length ≤ `max_options_m`
  - AC-STATUS-4: Returns `finished` with result after agent finalized
  - AC-STATUS-5: `known_questions_count=0` returns first question if available
  - AC-STATUS-6: Malformed `final_result` in state raises `500` with JSON error body
  - AC-STATUS-7: `last_served_index` updated atomically on question serve; failure is non-fatal

---

#### `GET ${prefix}/quiz/{quiz_id}/media` — Async-image snapshot

Image generation (synopsis, characters, final-result) is dispatched to FAL by
background tasks scheduled from `/quiz/start` and the finalisation path. Those
tasks persist URLs **into Postgres only** — they never write to the agent state
in Redis. The user-visible quiz flow MUST never block on image generation.

This endpoint exposes a lightweight, read-only snapshot of whatever has been
persisted so the FE can poll and surface images as they become available.

- **Auth:** None (no Turnstile, no session lock, no Redis access)
- **Path param:** `quiz_id` (UUID)
- **Request body:** None
- **Response `200`:** `QuizMediaResponse`
  ```json
  {
    "quizId": "UUID",
    "synopsisImageUrl": "string | null",
    "resultImageUrl": "string | null",
    "characters": [
      { "name": "string", "imageUrl": "string | null" }
    ]
  }
  ```
- **Errors:** None — endpoint always returns `200`. DB exceptions and missing
  rows fail soft to an empty snapshot so the FE can keep polling cheaply.
- **Internal logic:**
  - Single PK lookup on `session_history` by `session_id`.
  - `synopsisImageUrl` ← `category_synopsis->>'image_url'`.
  - `resultImageUrl` ← `final_result->>'image_url'`.
  - `characters[]` ← `character_set` JSONB array, in insertion order, with
    duplicate names collapsed to first occurrence and blank names dropped.
- **Acceptance:**
  - AC-MEDIA-1: Always returns `200` with the response shape, even when no
    `session_history` row exists yet (all URL fields `null`, `characters: []`).
  - AC-MEDIA-2: `synopsisImageUrl` reflects `session_history.category_synopsis.image_url` when present.
  - AC-MEDIA-3: `characters[]` is derived from `session_history.character_set`; preserves order, deduplicates by name, blanks dropped, includes entries whose `imageUrl` is still `null`.
  - AC-MEDIA-4: `resultImageUrl` reflects `session_history.final_result.image_url` when present.
  - AC-MEDIA-5: Endpoint is read-only and never raises 5xx; DB exceptions yield an empty snapshot instead.
  - AC-MEDIA-6: No Turnstile, no session lock, no Redis access — purely a DB read by primary key.

---

### 4.4 Results

#### `GET ${prefix}/result/{result_id}` — Shareable result page data

- **Auth:** None
- **Path param:** `result_id` (UUID = `session_id`)
- **Response `200`:** `ShareableResultResponse`
  ```json
  {
    "title": "string",
    "description": "string",
    "imageUrl": null,
    "category": "string",
    "createdAt": "ISO 8601 datetime"
  }
  ```
- **Errors:**
  - `404`: Result not found or session not completed
- **Acceptance:**
  - AC-RESULT-1: Returns `200` with all fields for a completed session
  - AC-RESULT-2: Returns `404` for incomplete or unknown session
  - AC-RESULT-3: `createdAt` is a valid ISO 8601 timestamp

---

### 4.5 Feedback

#### `POST ${prefix}/feedback` — Submit thumbs up/down + optional text

- **Auth:** Cloudflare Turnstile
- **Request body:** `FeedbackRequest` (Turnstile token stripped before Pydantic parse)
  ```json
  {
    "quizId": "UUID",
    "rating": "up" | "down",
    "text": "optional string, max 4096 chars",
    "cf-turnstile-response": "string, required"
  }
  ```
- **Response `204`:** No content
- **Errors:**
  - `400`: Missing Turnstile token
  - `401`: Turnstile failed
  - `404`: `quizId` not found
  - `422`: `rating` not `"up"` or `"down"`; `text` exceeds 4096 chars
  - `500`: DB save failed
- **Side effects:**
  - `session_history.user_sentiment` set to `POSITIVE` (up) or `NEGATIVE` (down)
  - `session_history.user_feedback_text` set to `text` (if provided)
- **Acceptance:**
  - AC-FEEDBACK-1: Valid `quizId` + `rating: "up"` + valid Turnstile → `204`
  - AC-FEEDBACK-2: Unknown `quizId` → `404`
  - AC-FEEDBACK-3: `text` > 4096 chars → `422`
  - AC-FEEDBACK-4: `rating` not `"up"` or `"down"` → `422`
  - AC-FEEDBACK-5: Missing Turnstile token → `400`
  - AC-FEEDBACK-6: Invalid Turnstile token → `401`
  - AC-FEEDBACK-7: After `204`, DB row has correct `user_sentiment` value

---

## 5. Data Structures & Schemas

### 5.1 API Request/Response Schemas

All defined in `app/models/api.py`. Use Pydantic v2. Response models use `model_config = ConfigDict(populate_by_name=True)` and camelCase aliases via `alias_generator=to_camel`.

#### `StartQuizRequest`
```python
class StartQuizRequest(BaseModel):
    category: str = Field(min_length=3, max_length=100)
    cf_turnstile_response: str = Field(alias="cf-turnstile-response")
```

#### `ProceedRequest`
```python
class ProceedRequest(BaseModel):
    quiz_id: UUID = Field(alias="quizId")
```

#### `NextQuestionRequest`
```python
class NextQuestionRequest(APIBaseModel):
    quiz_id: UUID                                          # alias: quizId
    question_index: int = Field(ge=0)
    answer: Optional[str] = Field(default=None, max_length=2048)
    option_index: Optional[int] = Field(default=None, ge=0, le=1000)

    @model_validator(mode="after")
    def _require_answer_or_option(self):
        # Raises ValueError if both answer (stripped to empty) and option_index are absent
        # answer="" counts as absent; option_index=0 counts as present
        ...
```

#### `FeedbackRequest`
```python
class FeedbackRequest(BaseModel):
    quiz_id: UUID = Field(alias="quizId")
    rating: Literal["up", "down"]
    text: str | None = Field(default=None, max_length=4096)
```

#### `FrontendStartQuizResponse`
```python
class SynopsisData(BaseModel):
    type: Literal["synopsis"] = "synopsis"
    title: str
    summary: str

class SynopsisPayload(BaseModel):
    type: Literal["synopsis"] = "synopsis"
    data: SynopsisData

class CharacterData(BaseModel):
    name: str
    short_description: str  # alias: shortDescription
    profile_text: str       # alias: profileText
    image_url: None = None  # alias: imageUrl

class CharactersPayload(BaseModel):
    type: Literal["characters"] = "characters"
    data: List[CharacterData]

class FrontendStartQuizResponse(BaseModel):
    quiz_id: UUID             # alias: quizId
    initial_payload: SynopsisPayload    # alias: initialPayload
    characters_payload: CharactersPayload  # alias: charactersPayload
```

#### `ProcessingResponse`
```python
class ProcessingResponse(BaseModel):
    status: Literal["processing"] = "processing"
    quiz_id: UUID  # alias: quizId
```

#### `QuizStatusQuestion`
```python
class QuestionOptionData(BaseModel):
    text: str
    image_url: None = None  # alias: imageUrl

class QuestionData(BaseModel):
    text: str
    image_url: None = None  # alias: imageUrl
    options: List[QuestionOptionData]

class QuizStatusQuestion(BaseModel):
    status: Literal["active"] = "active"
    type: Literal["question"] = "question"
    data: QuestionData
```

#### `QuizStatusResult`
```python
class ResultData(BaseModel):
    title: str
    image_url: None = None  # alias: imageUrl
    description: str

class QuizStatusResult(BaseModel):
    status: Literal["finished"] = "finished"
    type: Literal["result"] = "result"
    data: ResultData
```

#### `ShareableResultResponse`
```python
class ShareableResultResponse(APIBaseModel):
    title: str
    description: str
    image_url: Optional[str] = None  # alias: imageUrl
    category: Optional[str] = None
    created_at: Optional[str] = None  # alias: createdAt; ISO 8601 string
```

---

### 5.2 Internal Agent State Schema

`GraphState` is a `TypedDict` used by LangGraph. `AgentGraphStateModel` is its Pydantic mirror used for Redis serialization/validation.

```python
class GraphState(TypedDict):
    # Session identity
    session_id: str                # UUID string
    trace_id: str                  # UUID string for tracing
    category: str                  # Original user topic

    # LangChain messages (append-only via add_messages reducer)
    messages: Annotated[List[BaseMessage], add_messages]

    # Error tracking
    error_count: int
    error_message: str | None
    is_error: bool

    # Control flow
    ready_for_questions: bool      # Set by /proceed endpoint

    # Agent plan (from bootstrap)
    agent_plan: dict | None        # Serialized InitialPlan
    topic_analysis: dict | None    # Serialized TopicAnalysis from intent_classification

    # Generated content
    synopsis: dict | None          # {"title": str, "summary": str}
    ideal_archetypes: List[str]    # Canonical character type names from plan
    generated_characters: List[dict]  # List of CharacterProfile dicts
    generated_questions: List[dict]   # All questions generated so far

    # Quiz progress
    baseline_count: int            # Number of baseline questions requested
    baseline_ready: bool           # True once baseline questions exist in state
    quiz_history: List[dict]       # {"question_index": int, "answer": str | None, "option_index": int | None}
    should_finalize: bool          # True once decide_or_finish says FINISH_NOW
    current_confidence: float      # Latest confidence score from decision tool
    final_result: dict | None      # {"title": str, "description": str, "imageUrl": null}
    last_served_index: int         # Index of the last question returned by /status

    # RAG context
    rag_context: str | None        # Retrieved text from Wikipedia/web
    outcome_kind: str              # "character" | "archetype" | "personality" | ...
    creativity_mode: str           # "canonical" | "creative" | "mixed"
```

---

### 5.3 Database ORM Models

All defined in `app/models/db.py`. Use SQLAlchemy 2.0 declarative style with `DeclarativeBase`.

#### `Character`

| Column | SQLAlchemy Type | Python Type | Notes |
|--------|----------------|-------------|-------|
| `id` | `Uuid(as_uuid=True)` | `UUID` | PK, `server_default=text("uuid_generate_v4()")` |
| `name` | `Text` | `str` | UNIQUE, NOT NULL |
| `short_description` | `Text` | `str` | NOT NULL |
| `profile_text` | `Text` | `str` | NOT NULL |
| `profile_picture` | `LargeBinary` | `bytes \| None` | nullable |
| `image_url` | `Text` | `str \| None` | nullable; populated by FAL pipeline (§7.8) — must mirror DB column added by `init.sql` ALTER. AC-DB-ORM-1 |
| `judge_quality_score` | `SmallInteger` | `int \| None` | nullable |
| `judge_feedback` | `Text` | `str \| None` | nullable |
| `created_at` | `DateTime(timezone=True)` | `datetime` | `server_default=func.now()` |
| `last_updated_at` | `DateTime(timezone=True)` | `datetime` | `onupdate=func.now()` |

Relationships: `sessions` (many-to-many via `character_session_map`)

#### `SessionHistory`

| Column | SQLAlchemy Type | Python Type | Notes |
|--------|----------------|-------------|-------|
| `session_id` | `Uuid(as_uuid=True)` | `UUID` | PK |
| `category` | `Text` | `str` | NOT NULL |
| `category_synopsis` | `JSONB` | `dict` | NOT NULL |
| `synopsis_embedding` | `VECTOR(384)` | `list \| None` | nullable, IVFFlat cosine index |
| `agent_plan` | `JSONB` | `dict \| None` | nullable |
| `session_transcript` | `JSONB` | `dict` | NOT NULL |
| `character_set` | `JSONB` | `list` | NOT NULL, `server_default="'[]'::jsonb"`, GIN index |
| `final_result` | `JSONB` | `dict \| None` | nullable |
| `judge_plan_score` | `SmallInteger` | `int \| None` | nullable |
| `judge_plan_feedback` | `Text` | `str \| None` | nullable |
| `user_sentiment` | `Enum("POSITIVE","NEGATIVE","NONE")` | `str \| None` | nullable |
| `user_feedback_text` | `Text` | `str \| None` | nullable |
| `is_completed` | `Boolean` | `bool` | NOT NULL, `server_default=false()` |
| `completed_at` | `DateTime(timezone=True)` | `datetime \| None` | nullable |
| `qa_history` | `JSONB` | `dict \| None` | nullable |
| `created_at` | `DateTime(timezone=True)` | `datetime` | `server_default=func.now()` |
| `last_updated_at` | `DateTime(timezone=True)` | `datetime` | trigger-maintained |

Relationships: `characters` (many-to-many via `character_session_map`)

#### `character_session_map` (association table)

| Column | Type | Notes |
|--------|------|-------|
| `character_id` | UUID FK → `characters(id)` | ON DELETE CASCADE |
| `session_id` | UUID FK → `session_history(session_id)` | ON DELETE CASCADE |

PK: `(character_id, session_id)`

#### `SessionQuestions`

| Column | SQLAlchemy Type | Python Type | Notes |
|--------|----------------|-------------|-------|
| `session_id` | `Uuid(as_uuid=True)` | `UUID` | PK, FK → `session_history(session_id)` ON DELETE CASCADE |
| `baseline_questions` | `JSONB` | `list \| None` | nullable, GIN index |
| `adaptive_questions` | `JSONB` | `list \| None` | nullable, GIN index |
| `properties` | `JSONB` | `dict \| None` | nullable |
| `created_at` | `DateTime(timezone=True)` | `datetime` | `server_default=func.now()` |
| `last_updated_at` | `DateTime(timezone=True)` | `datetime` | trigger-maintained |

---

### 5.4 Redis Payloads

#### Quiz Session State

- **Key:** `quiz_session:{session_id}` (UUID string, no braces)
- **Type:** Redis String (JSON-serialized `AgentGraphStateModel`)
- **TTL:** 3600 seconds
- **Serialization:** `model.model_dump_json()` → stored as bytes
- **Deserialization:** `AgentGraphStateModel.model_validate_json(raw)` → deserialized; `None` returned on any error (fail-open)
- **Atomic update pattern:**
  - `WATCH quiz_session:{id}`
  - Read + deserialize current value
  - Apply update function to produce new state
  - `MULTI` → `SET quiz_session:{id} {new_value} EX 3600` → `EXECUTE`
  - On `WatchError`: retry up to 8 times with jittered exponential backoff (base 0.05s, cap 2s)

#### RAG Cache

- **Key:** `rag_cache:{category_slug}` (slugified category string)
- **Type:** Redis String
- **TTL:** 86400 seconds (24 hours)
- **Value:** raw retrieval text (string)
- **Operations:** simple GET/SET; any Redis error returns `None` (fail-open)

#### LangGraph Checkpoints

- **Key pattern:** Managed by `langgraph-checkpoint-redis` library
- **Purpose:** Persist graph execution state for resumable background tasks
- **Fallback:** `InMemorySaver` when Redis is unavailable at startup (local/dev/test)

---

## 6. Persistence Processes

### 6.1 PostgreSQL Operations

All DB access goes through repository classes in `app/services/database.py` using `AsyncSession`. Sessions are obtained via `AsyncSessionLocal` (created from the shared engine in `app.state`).

#### Session creation flow (`SessionRepository.upsert_session_after_synopsis`)

1. Begin async transaction
2. For each character in `generated_characters`:
   a. `INSERT INTO characters ... ON CONFLICT (name) DO UPDATE SET ...` (upsert by name)
   b. Collect returned `character.id`
3. `INSERT INTO session_history (session_id, category, category_synopsis, agent_plan, session_transcript, character_set, is_completed) VALUES ... ON CONFLICT (session_id) DO UPDATE SET ...`
4. Delete existing `character_session_map` rows for this `session_id`
5. Bulk-insert new `character_session_map` rows
6. Commit

#### Session completion (`SessionRepository.mark_completed`)

1. `UPDATE session_history SET final_result=?, qa_history=?, is_completed=TRUE, completed_at=now() WHERE session_id=?`
2. Commit

#### QA snapshot (`SessionRepository.update_qa_history`)

- Called after each `POST /quiz/next` as a best-effort fire-and-forget
- `UPDATE session_history SET qa_history=? WHERE session_id=?`
- Errors are caught and logged; never raise to the caller

#### Character repository

- `upsert_by_name`: `INSERT ... ON CONFLICT (name) DO UPDATE SET short_description=EXCLUDED.short_description, profile_text=EXCLUDED.profile_text, last_updated_at=now() RETURNING *`
- `get_by_id`: simple SELECT by PK
- `get_many_by_ids`: SELECT WHERE id = ANY(:ids)

#### Questions repository (`SessionQuestionsRepository`)

- `baseline_exists(session_id)`: SELECT EXISTS
- `upsert_baseline(session_id, questions)`: `INSERT ... ON CONFLICT (session_id) DO UPDATE SET baseline_questions=EXCLUDED.baseline_questions`
- `upsert_adaptive(session_id, questions)`: same pattern for adaptive

#### Result service (`ResultService.get_result_by_id`)

- `SELECT session_id, category, final_result, created_at FROM session_history WHERE session_id=? AND is_completed=TRUE`
- Returns `ShareableResultResponse` or `None`

---

### 6.2 Redis Operations

| Operation | Function | Atomicity | Error Behaviour |
|-----------|----------|-----------|----------------|
| Save new session state | `save_quiz_state` | Non-atomic SET | Logs and returns on validation/Redis errors (no raise) |
| Load session state | `get_quiz_state` | Non-atomic GET | Returns `None` on miss or any error (fail-open) |
| Atomic state update | `update_quiz_state_atomically` | WATCH/MULTI/EXEC | Up to 8 retries; returns `None` and logs `redis.state_update.gave_up` after retries exhausted |
| Get RAG cache | `get_rag_cache` | Non-atomic GET | Returns `None` on any error (fail-open) |
| Set RAG cache | `set_rag_cache` | Non-atomic SET | Swallows errors silently |

---

### 6.3 Session Lifecycle State Machine

```
[START]
  │
  ▼
POST /quiz/start
  │  ← Turnstile gated
  │  Agent: bootstrap + generate_characters
  │  DB: upsert session + characters
  │  Redis: save state (ready_for_questions=False)
  │
  ▼
[STATE: synopsis_generated]
  │
  ▼
POST /quiz/proceed
  │  Redis: set ready_for_questions=True (atomic)
  │  Background: generate_baseline_questions
  │
  ▼
[STATE: questions_generating]
  │
  ▼
GET /quiz/status/{id} → "processing" (while questions generating)
  │
  ▼
GET /quiz/status/{id} → "active" {question data} (once baseline[0] ready)
  │
  ▼
POST /quiz/next (repeat per question)
  │  Validates questionIndex == last_served_index + 1
  │  Redis: atomic append to quiz_history
  │  DB: best-effort qa_history snapshot
  │  Background: decide_or_finish → maybe generate_adaptive_question
  │
  ▼
GET /quiz/status/{id} → "active" | "processing" (between questions)
  │
  ▼ (after min_questions + confidence threshold OR max_total_questions)
GET /quiz/status/{id} → "finished" {result data}
  │  DB: session marked is_completed=TRUE, final_result written
  │
  ▼
[STATE: completed]
  │
  ▼
GET /result/{session_id}  ← shareable link
```

---

## 7. Agent System (LangGraph)

### 7.1 Graph Topology

Graph is defined in `app/agent/graph.py` using `langgraph.graph.StateGraph(GraphState)`.
Compiled once at startup via `create_agent_graph(settings, llm_service)` and stored in `app.state.agent_graph`.

```
START
  └──→ [bootstrap]
         └──→ [generate_characters]
                └──→ <_phase_router>
                       │
                       ├──"end" [ready_for_questions=False]────────────────→ END
                       │
                       ├──"baseline" [not baseline_ready]──→ [generate_baseline_questions]
                       │                                           └──→ [assemble_and_finish] → END
                       │
                       └──"adaptive" [answered >= baseline_count]
                              └──→ [decide_or_finish]
                                     │
                                     ├──"ask" [should_finalize=False]──→ [generate_adaptive_question]
                                     │                                          └──→ [assemble_and_finish] → END
                                     │
                                     └──"finish" [should_finalize=True]──→ [assemble_and_finish] → END
```

> **Important:** Each background agent run executes ONE pass through the graph. The graph does NOT loop internally. Multiple background invocations (one per `/quiz/next`) advance the graph step by step via LangGraph checkpointing. `generate_adaptive_question` generates at most ONE question per background run.

### 7.2 Node Specifications

#### `bootstrap` node (`_bootstrap_node`)

**Inputs from state:** `category`, `trace_id`, `session_id`

**Steps:**
1. `_analyze_topic_safe(category)` — wraps `analyze_topic()`; catches all exceptions; returns defaults on failure
2. Call `tool_plan_quiz` (LLM call) → `InitialPlan`
   - `InitialPlan.title`: quiz title (prefix stripped/added via `_ensure_quiz_prefix_helper`)
   - `InitialPlan.synopsis`: synopsis text
   - `InitialPlan.ideal_archetypes`: `List[str]` (archetype names)
   - `InitialPlan.ideal_count_hint`: optional int hint
3. Build `Synopsis` from plan
4. Get archetypes: canonical sets first (if available) → planner output → `_repair_archetypes_if_needed`
5. Fallback: `["The Analyst", "The Dreamer", "The Realist"]` if still empty
6. Build `agent_plan_json` dict
7. Write to state: `synopsis`, `ideal_archetypes`, `agent_plan`, `topic_analysis`, `outcome_kind`, `creativity_mode`

**Idempotency:** If `synopsis` already in state, skip and return unchanged.
**Error handling:** On exception, increment `error_count`, set `is_error=True`, `error_message`; fallback plan used.

---

#### `generate_characters` node (`_generate_characters_node`)

**Inputs from state:** `ideal_archetypes`, `agent_plan`, `category`, `synopsis`, `creativity_mode`, `rag_context`

**Steps:**
1. Batch attempt: call `tool_draft_character_profiles` (all archetypes at once) within `asyncio.wait_for(timeout=per_call_timeout_s)`; skipped if `len(archetypes) <= 1` **or** if `len(archetypes) > settings.quiz.batch_max_archetypes` (AC-PERF-CHAR-1 — the structured JSON output for large lists routinely exceeds the LLM's `max_output_tokens`, returning truncated JSON; we skip the doomed call to save ~30s)
2. Fill missing: for archetypes not returned by batch (or if batch failed/skipped), call individual `draft_character_profile` concurrently
3. Concurrency bounded by `min(4, len(archetypes))` unless `settings.quiz.character_concurrency` is explicitly set (default in `appconfig.local.yaml` is `8`, chosen to keep a typical 13-outcome quiz to two waves while staying well clear of the Gemini per-minute quota that triggers `503 ServiceUnavailable` cascades; see AC-PERF-CHAR-2)
4. Per-item retries: up to `settings.agent.max_retries` attempts; sleep `0.5 + 0.5 * attempt` seconds between retries
5. Name-locks each result (character mapped by name)
6. Write to state: `generated_characters` (list of CharacterProfile dicts)

**Acceptance:**
- AC-PERF-CHAR-1: `_try_batch_generation` MUST NOT invoke `tool_draft_character_profiles` when `len(archetypes) > settings.quiz.batch_max_archetypes`; it logs `characters_node.batch.skipped` with `reason="archetype_count_exceeds_batch_cap"` and returns a `name → None` map so the per-character path takes over.
- AC-PERF-CHAR-2: The default `settings.quiz.character_concurrency` MUST be ≤ 8. Higher values empirically trigger Gemini `503 ServiceUnavailable` cascades on quizzes with ≥ 13 archetypes, dropping ~40 % of profiles after retry exhaustion. The cap leaves headroom against the per-minute LLM quota while still completing a 13-outcome quiz in two short waves.

**Archetype repair** (in `_repair_archetypes_if_needed`, called from `bootstrap`):
- Triggers if: archetypes empty, `len < min_characters`, `len > max_characters`, or `names_only=True` and items don't look like names
- Calls `tool_generate_character_list.ainvoke` to regenerate
- Clamps result to `max_characters`
- Fallback: `["The Analyst", "The Dreamer", "The Realist"]` if still empty after repair

**Idempotency:** If `generated_characters` already has same count as `ideal_archetypes`, skip.

---

#### `generate_baseline_questions` node (`_generate_baseline_questions_node`)

**Precondition:** `ready_for_questions == True`

**Steps:**
1. Call `generate_baseline_questions` tool (single structured LLM call)
   - `n = settings.quiz.baseline_questions_n`
   - `max_options = settings.quiz.max_options_m`
   - Characters passed as context
2. Write to state: appends to `generated_questions`; sets `baseline_count = n`, `baseline_ready = True`
3. Persist to DB: `SessionQuestionsRepository.upsert_baseline(session_id, questions)`

---

#### `decide_or_finish` node (`_decide_or_finish_node`)

**Inputs from state:** `quiz_history`, `generated_questions`, `generated_characters`, `agent_plan`

**Steps:**
1. Call `decide_next_step` tool → `NextStepDecision`
   - `action: "FINISH_NOW" | "ASK_ONE_MORE_QUESTION"`
   - `confidence: float 0.0-1.0`
   - `winner_character: str | None`
   - `reasoning: str`
2. Apply business rules override:
   - `answered >= max_total_questions` → force `FINISH_NOW`
   - `answered < min_questions_before_early_finish` → force `ASK_ONE_MORE_QUESTION`
   - `action == FINISH_NOW AND confidence < early_finish_confidence` → force `ASK_ONE_MORE_QUESTION`
3. If `FINISH_NOW`:
   - Call `write_final_user_profile` tool → `FinalResult`
   - Write to state: `final_result`, `should_finalize = True`, `current_confidence`
   - Call `SessionRepository.mark_completed(session_id, final_result, qa_history)`
4. Else: write `current_confidence`, `should_finalize = False`

---

#### `generate_adaptive_question` node (`_generate_adaptive_question_node`)

**Steps:**
1. Call `generate_next_question` tool (single question) → `QuestionOut`
   - Context: quiz_history, generated_characters, current confidence, missing information
2. Append to `generated_questions`
3. Persist to DB: `SessionQuestionsRepository.upsert_adaptive(session_id, questions)`

---

#### `assemble_and_finish` node (`_assemble_and_finish`)

**Steps:**
- No-op sink node: logs final summary (question count, character count, final result if any)
- No state mutations

---

### 7.3 Routing Logic

#### `_phase_router(state)` — called after `generate_characters`

```python
if not state.get("ready_for_questions"):
    return "end"         # → END (user hasn't called /proceed yet)
elif not state.get("baseline_ready"):
    return "baseline"    # → generate_baseline_questions
elif len(state.get("quiz_history") or []) >= (state.get("baseline_count") or 0):
    return "adaptive"    # → decide_or_finish
else:
    return "end"         # → END (waiting for more answers)
```

Maps to edges: `"end"→END`, `"baseline"→generate_baseline_questions`, `"adaptive"→decide_or_finish`

#### Conditional after `decide_or_finish`

```python
if state.get("should_finalize"):
    return "finish"   # → assemble_and_finish
else:
    return "ask"      # → generate_adaptive_question
```

> There is NO separate `_adaptive_router`. After `generate_adaptive_question`, the edge is unconditional → `assemble_and_finish`. The decision to ask another question happens on the NEXT background run.

---

### 7.4 Tool Inventory

#### Planning Tools (`app/agent/tools/planning_tools.py`)

| Tool Function | Output Schema | Default Model |
|---------------|---------------|---------------|
| `tool_plan_quiz(category, topic_analysis, rag_context)` | `InitialPlan` | `gpt-4o-mini` (YAML: `gemini/gemini-2.5-flash`) |
| `tool_generate_character_list(archetypes, plan, category)` | `CharacterArchetypeList` | `gemini/gemini-2.5-flash` |
| `normalize_topic(category)` | `NormalizedTopic` | N/A (local) |

#### Content Creation Tools (`app/agent/tools/content_creation_tools.py`)

| Tool Function | Output Schema | Notes |
|---------------|---------------|-------|
| `draft_character_profiles(characters, plan, category)` | `List[CharacterProfile]` | Batch call, concurrent |
| `draft_character_profile(name, archetype, plan, category)` | `CharacterProfile` | Single-character fallback |
| `generate_baseline_questions(characters, plan, n, max_options)` | `QuestionList` | All baseline questions in one call |
| `generate_next_question(quiz_history, characters, confidence)` | `QuestionOut` | One adaptive question |
| `decide_next_step(quiz_history, characters, plan)` | `NextStepDecision` | Determines FINISH_NOW vs ASK_ONE_MORE |
| `write_final_user_profile(winner, quiz_history, characters)` | `FinalResult` | Final result text |

#### Data Tools (`app/agent/tools/data_tools.py`)

| Tool Function | Output | Notes |
|---------------|--------|-------|
| `wikipedia_search(query)` | `str` | Only when `retrieval.allow_wikipedia=True` |
| `web_search(query)` | `str` | Only when `retrieval.allow_web=True`; uses `o4-mini` Responses API |
| `consume_retrieval_slot(session_id)` | `bool` | Budget enforcement; max `retrieval.max_calls_per_run` per session |

#### Intent Classification (`app/agent/tools/intent_classification.py`)

| Function | Output | Notes |
|----------|--------|-------|
| `analyze_topic(category)` | `TopicAnalysis` | Pure local; no LLM; keyword/regex matching |

`TopicAnalysis` fields:
- `normalized_category: str`
- `outcome_kind: str` — `"characters"` \| `"types"` \| `"archetypes"` \| `"profiles"`
- `creativity_mode: str` — `"whimsical"` \| `"balanced"` \| `"factual"`
- `names_only: bool`
- `intent: str`
- `domain: str`

---

### 7.5 LLM Tool Models

Default (hardcoded in `_DEFAULTS`): `gpt-4o-mini` for all tools.
Local override (YAML): `gemini/gemini-2.5-flash` for all tools.

`ModelConfig` fields:
```python
class ModelConfig(BaseModel):
    model: str
    temperature: float = 0.7
    max_output_tokens: int = 4096
    timeout_s: float = 30.0
    json_output: bool = True
    effort: str | None = None          # for o-series models: "low"|"medium"|"high"
    tool_choice: str | None = None     # "auto"|"required"|"none"
```

All LLM calls go through `LLMService.get_structured_response()` which uses the LiteLLM Responses API.

---

### 7.6 Prompts

Managed by `PromptManager` in `app/agent/prompts.py`. Each tool has a system prompt and zero or more user prompt templates loaded from `settings.llm_prompts`. Override-able via YAML.

Key prompts:
- `plan_quiz`: Instructs the model to analyse the topic, produce a quiz plan with archetypes, write a synopsis
- `draft_character_profile`: Instructs per-character profile generation (name, short_description, profile_text)
- `generate_baseline_questions`: Instructs generating N balanced MCQ questions distinguishing archetypes
- `generate_next_question`: Instructs generating one targeted adaptive question to refine confidence
- `decide_next_step`: Instructs the model to evaluate quiz_history vs character profiles and decide FINISH/ASK_MORE
- `write_final_user_profile`: Instructs generating a personalised result paragraph

Optional retrieval placeholders (already plumbed in templates and **honoured at runtime when adaptive research fires** — see §7.7):
- `{search_context}` — populated for the topic normalizer, planner, and character-list generator when grounding is run.
- `{character_context}` / `{character_contexts}` — populated for profile writer / batch profile writer when grounding is run.
- `{quiz_history}` — populated for the next-question generator and decision maker.

When grounding context is empty, prompts must continue to behave correctly (current behaviour: produce plausible content from the model's prior).

---

### 7.7 Adaptive Topic Research & Model Tiering

> **Goal:** The agent must be **fast on well-known topics** (no external research, cheapest competent models) and **thoughtful on fringe topics** (light external research, more capable models for content nodes). Research must be **bounded** in calls and latency.

#### 7.7.1 Topic Knowledge Classification

A new lightweight classifier runs as the **first sub-step** of the `bootstrap` node, immediately after `analyze_topic` (which is a local, no-LLM heuristic). It returns a `TopicKnowledgeAssessment`:

```python
class TopicKnowledgeAssessment(StrictBase):
    knowledge_score: float          # 0.0 (fringe / unknown) … 1.0 (encyclopedic / canonical)
    is_well_known: bool             # convenience flag, derived from threshold
    rationale: str                  # one-line explanation
    recommended_research: bool      # True iff knowledge_score < well_known_threshold
                                    # AND retrieval policy permits research
```

Implementation:
- New tool `classify_topic_knowledge(category, analysis)` in `app/agent/tools/planning_tools.py`.
- New LLM tool config slot `topic_knowledge_classifier` in `appconfig.local.yaml`. Default model: `gemini/gemini-2.5-flash-lite` (fast, cheap, deterministic). Temperature ≤ 0.1, `max_output_tokens ≤ 200`, `timeout_s ≤ 8`.
- A **fast-path short-circuit** sets `is_well_known=True, knowledge_score=1.0` (no LLM call) when ANY of:
  1. `canonical_for(category)` returns a non-empty list (handled set, e.g. MBTI, Hogwarts).
  2. `topic_analysis.creativity_mode == "factual"` AND `topic_analysis.domain` is in `{"frameworks_types_systems", "serious_professions_profiles"}`.
  3. `topic_analysis.is_media is True` AND the title length ≤ 40 chars and contains common franchise keywords (heuristic; see implementation).
- Failure of the LLM call defaults to `knowledge_score=1.0` (skip research) — fail-open for latency, never fail-closed for quality on unknowns by accident.

#### 7.7.2 Adaptive Research Tool

A single async function `gather_topic_research(category, analysis, *, trace_id, session_id)` performs **at most one** grounded query. Provider order is:

1. **Primary — Gemini Google Search grounding:** When the topic-knowledge classifier returns `recommended_research=True` and `settings.retrieval.allow_web is True`, the tool calls the `gemini/<model>` chat completion via LiteLLM with `tools=[{"googleSearch": {}}]`. The text synthesis returned by Gemini is captured.
2. **Fallback — OpenAI Responses `web_search`:** Only used if the Gemini call raises or returns an empty string. Uses the existing `web_search` tool plumbing (provider switch in `data_tools._build_web_search_spec`).
3. **Soft fallback — Wikipedia:** If both providers fail or are unavailable, and `allow_wikipedia=True`, run `wikipedia_search` once.

Budget and policy:
- `settings.retrieval.policy` gains a new permitted value **`"adaptive"`** (in addition to `off`, `media_only`, `auto`). Under `adaptive`, decisions are driven by the classifier rather than topic shape.
- `settings.retrieval.max_calls_per_run` defaults to `2` under `adaptive` — at most one bootstrap research call and at most one optional refinement.
- A per-run latency budget `settings.retrieval.research_latency_budget_s` (default `8.0`) caps the total time spent in research; on overrun, the partial result (or empty string) is returned and downstream nodes proceed.

Storage:
- The research synthesis is stored in `GraphState` under new keys:
  - `topic_knowledge: Optional[TopicKnowledgeAssessment]`
  - `research_context: Optional[str]` (compact, ≤ 4 KB, scrubbed)
  - `research_used: Optional[bool]` (true iff at least one grounded query ran)
  - `research_provider: Optional[Literal["gemini_grounding","openai_web_search","wikipedia","none"]]`
- `research_context` is fed to:
  - `topic_normalizer`, `initial_planner`, `character_list_generator` (`{search_context}`)
  - `profile_writer`, `profile_batch_writer` (`{character_context}` / `{character_contexts}`)

#### 7.7.3 Model Tiering

Per-tool models in `appconfig.local.yaml` are tiered as follows. The tiering is selected statically at config time **except** for two tools (`profile_batch_writer`, `question_generator`) where the runtime selection between Flash and Pro/3.x is driven by `state.topic_knowledge.is_well_known`.

| Tier | Model (default) | Tools (default assignment) |
|------|-----------------|----------------------------|
| **Lite** (cheapest, ≤ 1s) | `gemini/gemini-2.5-flash-lite` | `topic_knowledge_classifier`, `safety_checker`, `error_analyzer`, `failure_explainer`, `decision_maker`, `image_prompt_enhancer`, `character_selector` |
| **Flash** (default, balanced) | `gemini/gemini-2.5-flash` | `initial_planner`, `character_list_generator`, `synopsis_generator`, `profile_writer`, `profile_improver`, `next_question_generator`, `final_profile_writer`, `web_search` |
| **Pro / 3.x** (deeper reasoning) | `gemini/gemini-2.5-pro` (with `gemini/gemini-3.0-pro-preview` allowed if available) | `profile_batch_writer`, `question_generator` — **only when** `state.topic_knowledge.is_well_known is False` |

Mechanism:
- `invoke_structured(tool_name=...)` resolves the model from per-tool config.
- For the two adaptive tools, callers pass an `overrides={"model": ...}` kwarg that selects between the configured `model` (Flash) and a new `model_unknown` field (Pro / 3.x). The graph node looks at `state["topic_knowledge"]["is_well_known"]` to decide.
- Tier model strings are **never hard-coded in Python** — only in `appconfig.local.yaml` so they can be tuned without code changes.

#### 7.7.4 Speed-Mode vs Depth-Mode Prompt Variants

Where it materially helps, the prompt registry exposes a tone-tunable variant:

- `*_speed`: identical contract but shorter, no "use the research context" preamble (skipped because there is none on well-known topics). Selected when `is_well_known is True`.
- `*_depth`: identical contract, with explicit "use the research context as ground truth, do not contradict it; if context is empty, skip silently" preamble.

Implementation note: only `initial_planner`, `character_list_generator`, `profile_batch_writer`, and `question_generator` get these variants in v1. All others use the default.

#### 7.7.5 Evaluation Harness

A new test/eval harness lives under `backend/tests/eval/` (marked `@pytest.mark.eval` so it is excluded from default `pytest` runs):

- `tests/eval/topics.yaml` — curated set of ≥ 20 topics across three buckets:
  - **Well-known** (`bucket: known`): MBTI, Hogwarts Houses, Star Wars Characters, Western Zodiac, etc.
  - **Mid-tail** (`bucket: mid`): "Premier League goalkeepers", "Studio Ghibli protagonists", "K-pop fourth-gen groups".
  - **Fringe** (`bucket: fringe`): "Characters in 'Le Saga des Anneaux'", "Niche tabletop RPG classes", invented IPs.
- `tests/eval/run_eval.py` — async runner that:
  - Compiles the agent graph via `create_agent_graph()`.
  - Invokes `bootstrap` and `generate_characters` for each topic, then a 6-question baseline.
  - Captures, per topic: latency p50/p95 of bootstrap, total LLM call count, `research_used`, `research_provider`, character count, character-name uniqueness ratio, baseline-question count, baseline option count distribution.
  - Quality is judged by an LLM-judge (small Flash-Lite call) on a 1–5 scale across three rubrics: **on-topic-ness**, **archetype distinctness**, **question discriminative-ness**. Cached per (topic, iteration) to avoid re-judging unchanged outputs.
  - Persists results as JSONL under `tests/eval/results/{iteration_tag}.jsonl` and aggregate summary under `tests/eval/results/{iteration_tag}.summary.json`.
- The harness is **opt-in via env var** `RUN_LIVE_EVAL=1` to prevent accidental spend in CI / pre-commit.

Iteration discipline:
- Iteration `baseline-{date}` MUST be captured before any prompt or model change.
- Each subsequent iteration changes **at most two variables** (e.g. one prompt + one model swap) and is committed with a summary in the commit body referencing the JSONL artifact.
- A regression on either a) latency p95 by > 25 % or b) average quality score by > 0.3 vs baseline blocks merging the iteration.

---

### 7.8 Image Generation (FAL)

**Goal:** every quiz delivers a synopsis image, one image per character, and one final-result image. Speed and non-blocking behaviour are the top priorities; fidelity is explicitly secondary to **stylistic consistency**.

#### 7.8.1 Provider & model

- Provider: **fal.ai** via the `fal-client` Python SDK. Auth via env var `FAL_KEY` (also accepts legacy `FAL_AI_KEY` / `FAL_AI_API_KEY` for compatibility with the elf-BE secret store).
- Default model: **`fal-ai/flux/schnell`** at `image_size={"width":512,"height":512}` (tuned for sub-3 s end-to-end). Configurable via `image_gen.model` and `image_gen.image_size` in `appconfig.local.yaml`.
- **Per-subject aspect overrides** (AC-IMG-ASPECT-1/2): `generate_synopsis_image` and `generate_result_image` request `image_size={"width":1024,"height":576}` (16:9 landscape) so the generated hero matches the landscape display containers on the synopsis and results pages and avoids vertical cropping. Character images continue to use the configured default (square portrait).
- Returned URLs are FAL CDN URLs and are stored verbatim — we do **not** re-host. (If FAL CDN ever becomes unavailable we will add Azure Blob mirroring; this is out of scope for v1.)

#### 7.8.2 Configuration (`ImageGenSettings`)

| Field | Default | Purpose |
|---|---|---|
| `enabled` | `True` | Master switch; when False the pipeline returns instantly. |
| `provider` | `"fal"` | Currently only `fal` is supported. |
| `model` | `"fal-ai/flux/schnell"` | FAL model id. |
| `image_size` | `{"width":512,"height":512}` | Low-res for speed. |
| `num_inference_steps` | `2` | Schnell minimum for usable output. |
| `timeout_s` | `15.0` | Hard per-image timeout (asyncio.wait_for). |
| `concurrency` | `4` | Max concurrent FAL calls per session. |
| `style_suffix` | `"flat illustrated portrait, soft lighting, muted palette, consistent illustrated style, no text"` | Appended to every prompt for cross-image style consistency. |
| `negative_prompt` | `"text, watermark, logo, signature, blurry, deformed, extra limbs, low quality"` | Default safety/quality filter. |

#### 7.8.3 Prompt builder (`app/agent/tools/image_tools.py`)

Pure functions; no LLM call by default (keeps latency low):

- `build_character_image_prompt(profile, *, category, analysis, style_suffix)` — uses `profile.short_description` and `profile.profile_text` (truncated) to produce a **descriptive, IP-safe** prompt. Names that match canonical IP characters are NEVER passed verbatim; only descriptive tokens (hair colour, attire, era, expression).
- `build_synopsis_image_prompt(synopsis, *, category, analysis, style_suffix)` — abstract / symbolic illustration of the topic (no characters).
- `build_result_image_prompt(result, *, category, character_set, style_suffix)` — favours the matched character's `short_description` if found in `character_set`, else falls back to `result.title`/`description`.

All builders produce `{prompt, negative_prompt}` dicts and gracefully degrade when fields are missing.

#### 7.8.4 FAL client (`app/services/image_service.py`)

- `FalImageClient.generate(prompt, *, negative_prompt, model, image_size, timeout_s) -> Optional[str]`
- Uses `fal_client.subscribe_async` wrapped in `asyncio.wait_for(timeout_s)`.
- **Always returns `Optional[str]`**: returns `None` on any exception/timeout; never raises. Logs failures structured under `image.fal.fail`.
- Rate-limit / concurrency guarded by an `asyncio.Semaphore(concurrency)` shared per process.

#### 7.8.5 Pipeline & persistence (`app/services/image_pipeline.py`)

- `generate_character_images(session_id, characters, category, analysis)` — async fan-out (gather with semaphore). Returns a `dict[name, url]`.
- `generate_synopsis_image(session_id, synopsis, category, analysis)` and `generate_result_image(session_id, result, character_set)` — single-image helpers.
- All three are scheduled via FastAPI `BackgroundTasks` from the quiz endpoints **after** the primary persistence call returns. They never block the user-visible response.
- On completion, each helper UPDATEs the row that owns it:
  - Characters: `UPDATE characters SET image_url = $1 WHERE name = $2 AND image_url IS NULL`
  - Session synopsis: `UPDATE session_history SET category_synopsis = jsonb_set(category_synopsis, '{image_url}', $1)`
  - Final result: `UPDATE session_history SET final_result = jsonb_set(final_result, '{image_url}', $1)`
  - `character_set` snapshot is rebuilt to include the new `image_url` per element.

#### 7.8.6 DB schema additions

Idempotent ALTER TABLE in `db/init/init.sql`:

```sql
ALTER TABLE characters ADD COLUMN IF NOT EXISTS image_url TEXT NULL;
```

The `synopsis` and `final_result` images live inside their existing JSONB columns — no new columns needed.

#### 7.8.7 Frontend behaviour

- All `image_url` fields are already optional. The FE renders a CSS placeholder when `image_url` is absent and swaps in the real image with a fade transition once it loads.
- The FE polls (or refetches on idle) the existing status / result endpoints; no new endpoints are required.

---

## 8. Services Layer

### `CharacterRepository` (`services/database.py`)

```python
async def get_by_id(session: AsyncSession, character_id: UUID) -> Character | None
async def get_many_by_ids(session: AsyncSession, ids: List[UUID]) -> List[Character]
async def create(session: AsyncSession, **kwargs) -> Character
async def upsert_by_name(session: AsyncSession, name: str, short_description: str, profile_text: str) -> Character
async def update_profile(session: AsyncSession, character_id: UUID, **kwargs) -> Character | None
async def set_profile_picture(session: AsyncSession, character_id: UUID, image_data: bytes) -> None
```

### `SessionRepository`

```python
async def get_by_id(session: AsyncSession, session_id: UUID) -> SessionHistory | None
async def upsert_session_after_synopsis(
    session: AsyncSession,
    session_id: UUID,
    category: str,
    synopsis: dict,
    agent_plan: dict | None,
    characters: List[dict],
    transcript: dict,
    character_set: List[dict],
) -> SessionHistory
async def mark_completed(
    session: AsyncSession,
    session_id: UUID,
    final_result: dict,
    qa_history: dict | None,
) -> None
async def update_qa_history(session: AsyncSession, session_id: UUID, qa_history: dict) -> None
async def save_feedback(session: AsyncSession, session_id: UUID, rating: str, text: str | None) -> None
```

### `SessionQuestionsRepository`

```python
async def get_for_session(session: AsyncSession, session_id: UUID) -> SessionQuestions | None
async def baseline_exists(session: AsyncSession, session_id: UUID) -> bool
async def upsert_baseline(session: AsyncSession, session_id: UUID, questions: List[dict]) -> None
async def upsert_adaptive(session: AsyncSession, session_id: UUID, questions: List[dict]) -> None
```

### `ResultService`

```python
async def get_result_by_id(session: AsyncSession, result_id: UUID) -> ShareableResultResponse | None
def normalize_final_result(raw: dict | object) -> dict
```

`normalize_final_result` handles:
- Pydantic model input → `.model_dump()`
- Legacy field names: `imageUrl`/`image_url`, `profileTitle`/`title`, `summary`/`description`
- Always sets `imageUrl = null`

### `CacheRepository` (`services/redis_cache.py`)

```python
async def save_quiz_state(redis: Redis, session_id: str, state: AgentGraphStateModel) -> None
async def get_quiz_state(redis: Redis, session_id: str) -> AgentGraphStateModel | None
async def update_quiz_state_atomically(
    redis: Redis,
    session_id: str,
    update_fn: Callable[[AgentGraphStateModel], AgentGraphStateModel],
    max_retries: int = 8,
) -> AgentGraphStateModel
async def get_rag_cache(redis: Redis, category: str) -> str | None
async def set_rag_cache(redis: Redis, category: str, content: str) -> None
```

`update_quiz_state_atomically` returns `None` and logs `redis.state_update.gave_up` after `max_retries` exhausted (does NOT raise an exception — callers must handle `None` return).

### `LLMService` (`services/llm_service.py`)

```python
async def get_structured_response(
    tool_name: str,
    messages: List[BaseMessage],
    response_model: Type[T],
    model_config: ModelConfig,
) -> T
```

Internal steps:
1. Convert `BaseMessage` list to LiteLLM format
2. Call LiteLLM Responses API with configured model + timeout
3. Extract JSON from response output items (type `"message"`)
4. Strip code fences (`` ```json ... ``` ``)
5. Scan for balanced JSON block if needed
6. Validate with `response_model.model_validate_json(raw)`
7. Raise `StructuredOutputError` on any extraction or validation failure

### Magic Numbers Reference

All hardcoded constants, with their source and whether they are configurable:

| Constant | Value | Configurable via | Location |
|----------|-------|-----------------|----------|
| Request body limit | 256 KiB (262144 bytes) | `MAX_REQUEST_BODY_BYTES` | `main.py` |
| Readiness probe timeout | 2.0s | `READINESS_PROBE_TIMEOUT_S` | `main.py` |
| Config YAML max size | 1 MiB (1048576 bytes) | `CONFIG_MAX_BYTES` | `config.py` endpoint |
| Config cache seconds | 60s | `CONFIG_CACHE_SECONDS` | `config.py` endpoint |
| Turnstile token max length | 4096 chars | hardcoded | `dependencies.py` |
| Turnstile HTTP total timeout | 5.0s | hardcoded | `dependencies.py` |
| Turnstile HTTP connect timeout | 3.0s | hardcoded | `dependencies.py` |
| Redis pool max connections | 50 | hardcoded | `dependencies.py` |
| Redis pool wait timeout | 10s | hardcoded | `dependencies.py` |
| Redis socket connect timeout | 5s | hardcoded | `dependencies.py` |
| Redis per-command timeout | 5s | hardcoded | `dependencies.py` |
| Redis health check interval | 30s | hardcoded | `dependencies.py` |
| Redis client retry count | 3 | hardcoded | `dependencies.py` |
| Quiz session Redis TTL | 3600s (1h) | hardcoded | `redis_cache.py` |
| RAG cache Redis TTL | 86400s (24h) | hardcoded | `redis_cache.py` |
| Atomic update max retries | 8 | hardcoded | `redis_cache.py` |
| Jitter backoff base | 0.05s | hardcoded | `redis_cache.py` |
| Jitter backoff cap | 0.5s | hardcoded | `redis_cache.py` |
| DB pool size | 10 | hardcoded | `dependencies.py` |
| DB max overflow | 5 | hardcoded | `dependencies.py` |
| Category min length | 3 chars | Pydantic model | `models/api.py` |
| Category max length | 100 chars | Pydantic model | `models/api.py` |
| Answer text max length | 2048 chars | Pydantic model | `models/api.py` |
| Feedback text max length | 4096 chars | Pydantic model | `models/api.py` |
| `option_index` upper bound | 1000 | Pydantic model | `models/api.py` |
| First step timeout (local YAML effective) | 90.0s | `quiz.first_step_timeout_s` YAML | `config.py` |
| Stream budget | 30.0s | `quiz.stream_budget_s` YAML | `config.py` |
| Min characters (local YAML effective) | 2 | `quiz.min_characters` YAML | `config.py` |
| Max characters (local YAML effective) | 32 | `quiz.max_characters` YAML | `config.py` |
| Baseline questions (local YAML effective) | 6 | `quiz.baseline_questions_n` YAML | `config.py` |
| Max options per question (local YAML effective) | 6 | `quiz.max_options_m` YAML | `config.py` |
| Max total questions (local YAML effective) | 15 | `quiz.max_total_questions` YAML | `config.py` |
| Min questions before early finish (local YAML effective) | 8 | `quiz.min_questions_before_early_finish` YAML | `config.py` |
| Early finish confidence (local YAML effective) | 0.85 | `quiz.early_finish_confidence` YAML | `config.py` |
| Agent max retries | 3 | `agent.max_retries` YAML | `config.py` |
| Per-call LLM timeout | 30s | `llm.per_call_timeout_s` YAML | `config.py` |
| Character gen retry sleep | `0.5 + 0.5 * attempt` seconds | hardcoded | `graph.py` |
| Slow LLM threshold | 2000ms | `LLM_SLOW_MS` | `logging_config.py` |
| CORS max_age | 600s | hardcoded | `main.py` |
| HSTS max-age | 31536000s (1yr) | hardcoded | `main.py` |
| LangGraph Redis TTL | 60 min | `LANGGRAPH_REDIS_TTL_MIN` | `graph.py` |

---

## 8A. Spring 2026 Frontend UX Compatibility Note

The Spring 2026 UX refinements are frontend presentation-layer changes only (header/footer touch ergonomics, hero surface depth treatment, topic suggestion affordance clarity, question-progress microcopy).

Backend impact:
- No route changes.
- No request/response schema changes.
- No persistence behavior changes.
- No new configuration keys required.

Compatibility acceptance criteria:
- AC-BE-UX26-CFG-1: Existing `GET /config` schema remains sufficient for FE refinement rollout.
- AC-BE-UX26-CFG-2: Existing `content.landingPage` and `theme` payloads continue to support FE copy/token rendering without contract drift.

---

## 9. Security

### 9.1 Cloudflare Turnstile

**Applied to:** `POST /quiz/start`, `POST /feedback`

**Verification flow:**
1. Extract `cf-turnstile-response` from JSON body
2. Validate non-null, non-empty string ≤ 4096 chars; else `400`
3. If `settings.security.enabled == False` OR `settings.security.turnstile.secret_key` is empty: bypass (return OK) — for local/dev
4. POST to `https://challenges.cloudflare.com/turnstile/v0/siteverify` with form fields `secret=<key>&response=<token>`
   - Timeout: total 5s, connect 3s (httpx)
5. Parse JSON response: `{"success": bool}`
6. `success == True` → pass
7. `success == False` → `401 {"detail": "Turnstile verification failed."}`
8. Any network error → `500 {"detail": "Turnstile verification service error."}`

**Acceptance criteria:**
- AC-SEC-TURNSTILE-1: Valid token in prod → passes through
- AC-SEC-TURNSTILE-2: Invalid token → `401`
- AC-SEC-TURNSTILE-3: Missing token → `400`
- AC-SEC-TURNSTILE-4: Token > 4096 chars → `400`
- AC-SEC-TURNSTILE-5: `ENABLE_TURNSTILE=false` → bypass (token still accepted but not verified)
- AC-SEC-TURNSTILE-6: Turnstile service unreachable → `500` (not `401`)
- AC-SEC-TURNSTILE-7: Token never logged

---

### 9.2 CORS

**Middleware:** `starlette.middleware.cors.CORSMiddleware`

| Parameter | Value |
|-----------|-------|
| `allow_origins` | From `ALLOWED_ORIGINS` env var (JSON array or CSV); fallback `["http://localhost:5173","http://127.0.0.1:5173"]` |
| `allow_methods` | `["GET", "POST", "DELETE", "OPTIONS"]` |
| `allow_headers` | `Accept, Accept-Language, Content-Language, Content-Type, Authorization, X-Trace-ID, X-Request-ID, X-Turnstile-Token, traceparent, tracestate` |
| `expose_headers` | `X-Trace-ID, traceparent, Server-Timing` |
| `allow_credentials` | `True` |
| `max_age` | `600` |

**`ALLOWED_ORIGINS` parsing** (`_read_allowed_origins` in `main.py`):
- Handles: JSON array `["https://..."]`, CSV `https://a,https://b`, bracketed unquoted `[https://a, https://b/]`, Azure escaped-quote variant `[\"https://...\"]`
- Always strips trailing `/` from each origin
- Normalises `\"` → `"` before JSON parse attempt

**Acceptance criteria:**
- AC-SEC-CORS-1: Preflight `OPTIONS` from allowed origin → `200` with correct `Access-Control-Allow-Origin`
- AC-SEC-CORS-2: Preflight from disallowed origin → no `Access-Control-Allow-Origin` header
- AC-SEC-CORS-3: Actual request from allowed origin → `Access-Control-Allow-Origin` in response
- AC-SEC-CORS-4: `ALLOWED_ORIGINS` as JSON array → parsed correctly
- AC-SEC-CORS-5: `ALLOWED_ORIGINS` as CSV → parsed correctly
- AC-SEC-CORS-6: `ALLOWED_ORIGINS` with escaped quotes → parsed correctly

---

### 9.3 Security Headers

Applied by `SecurityHeadersMiddleware` (custom) on every response.

| Header | Value |
|--------|-------|
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY` |
| `Referrer-Policy` | `strict-origin-when-cross-origin` |
| `Permissions-Policy` | `geolocation=(), microphone=(), camera=()` |
| `Content-Security-Policy` | `default-src 'none'; frame-ancestors 'none'; base-uri 'none'` |
| `Cross-Origin-Opener-Policy` | `same-origin` |
| `Cross-Origin-Resource-Policy` | `same-origin` |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` (non-local environments only) |

**Acceptance criteria:**
- AC-SEC-HEADERS-1: All 7 headers present on every response (8 on non-local envs)
- AC-SEC-HEADERS-2: `HSTS` absent on `APP_ENVIRONMENT=local`
- AC-SEC-HEADERS-3: `X-Frame-Options: DENY` prevents iframe embedding

---

### 9.4 Request Body Size Limit

**Middleware:** `body_size_limit_middleware` (custom async middleware function, not a class)

| Parameter | Value |
|-----------|-------|
| Max bytes | `MAX_REQUEST_BODY_BYTES` env var, default `262144` (256 KiB) |
| Skipped methods | `GET`, `HEAD`, `OPTIONS`, `DELETE` |

**Behaviour:**
- Skips size check entirely for `GET/HEAD/OPTIONS/DELETE`
- Checks `Content-Length` header first; if present and > limit → `413` immediately (body not read); adds `Connection: close`
- Invalid `Content-Length` (non-numeric) → `400`
- For chunked/missing `Content-Length`: reads up to `limit + 1` bytes; if exceeded → `413`
- Response: `413 {"detail": "Request body too large.", "errorCode": "PAYLOAD_TOO_LARGE"}`

**Acceptance criteria:**
- AC-SEC-BODY-1: Body exactly at limit → accepted
- AC-SEC-BODY-2: Body 1 byte over limit → `413`
- AC-SEC-BODY-3: `Content-Length` header > limit → `413` without reading body
- AC-SEC-BODY-4: `errorCode: "PAYLOAD_TOO_LARGE"` present in `413` body

---

### 9.5 OpenAPI Docs Gating

- Docs gating is determined at **module import time** via `os.getenv("APP_ENVIRONMENT")` (not from `Settings` object)
- `_DOCS_ENABLED = (os.getenv("APP_ENVIRONMENT") or "local").lower() in {"local","dev","development","test","testing"}`
- When `_DOCS_ENABLED=False`: `docs_url=None`, `redoc_url=None`, `openapi_url=None` → FastAPI does not register those routes → `404`
- When `_DOCS_ENABLED=True`: Swagger UI at `/docs`, ReDoc at `/redoc`, OpenAPI schema at `/openapi.json`
- Root `GET /` redirects to `/docs` (HTTP 307) when `_DOCS_ENABLED=True`, returns `{"status":"ok"}` when False

**Acceptance criteria:**
- AC-SEC-DOCS-1: `/docs`, `/redoc`, `/openapi.json` return `404` when `APP_ENVIRONMENT=production`
- AC-SEC-DOCS-2: `/docs` returns `200` when `APP_ENVIRONMENT=local`
- AC-SEC-DOCS-3: `GET /` returns `{"status":"ok"}` (not redirect) when `APP_ENVIRONMENT=production`

---

### 9.6 Log Redaction

`RedactFilter` (stdlib) and `redact_processor` (structlog) scrub these keys from all log events:

```
authorization, api-key, apikey, openai-api-key, cf-turnstile-response,
password, secret, token
```

**Acceptance criteria:**
- AC-SEC-LOGS-1: `cf-turnstile-response` never appears in logs
- AC-SEC-LOGS-2: `secret` key values replaced with `"[REDACTED]"`
- AC-SEC-LOGS-3: `token` key values replaced with `"[REDACTED]"`

---

### 9.7 Phase 9 — Production Hardening

This subsection captures the hardening pass tracked alongside the §16 reliability work. ACs are grouped by area; tests live under `tests/security/` and `tests/integration/` (and `frontend/tests/e2e/security.spec.ts` for cross-stack validation).

#### 9.7.1 Image URL Validation (LLM/FAL → DB → FE)

The image URL returned by FAL is treated as **untrusted input**. It must be validated before persistence so that malicious or malformed values can never reach the frontend `<img src>`.

`app/services/image_service.py::_validate_image_url(url)` returns the URL when:
- scheme is exactly `https://` (case-insensitive); and
- host matches the configured allowlist (`image_gen.url_allowlist`, default `["fal.media", "v3.fal.media", "v2.fal.media"]`, with subdomain match);

…otherwise returns `None` and emits `image.url.rejected` at `info` level with `reason` and (truncated) `host`.

**Acceptance criteria:**
- AC-IMG-URL-1: FAL response containing `images[0].url = "javascript:alert(1)"` → `generate()` returns `None`; `image.url.rejected` logged with `reason="bad_scheme"`.
- AC-IMG-URL-2: FAL response with `url = "http://fal.media/x.png"` (plain HTTP) → rejected; `reason="bad_scheme"`.
- AC-IMG-URL-3: FAL response with `url = "https://evil.com/x.png"` → rejected; `reason="host_not_allowed"`.
- AC-IMG-URL-4: FAL response with `url = "https://v3.fal.media/files/abc.png"` → accepted unchanged.
- AC-IMG-URL-5: FAL response with `url = "https://sub.fal.media/x.png"` → accepted (subdomain of allowlisted host).
- AC-IMG-URL-6: `url = "data:image/png;base64,xxx"` → rejected.
- AC-IMG-URL-7: Allowlist is configurable via `image_gen.url_allowlist`; an empty list disables host check (scheme check still applies).

#### 9.7.2 Frontend Image URL Hygiene

Mirror of §9.7.1 on the FE: even if a malicious URL slips past the BE, the FE must refuse to render it.

`frontend/src/utils/safeImageUrl.ts::safeImageUrl(url, opts?)` returns the URL when it is an `https://` URL with a host whose effective TLD+1 matches the allowlist (`['fal.media']` by default, plus dev override via `import.meta.env.VITE_IMAGE_URL_ALLOWLIST`). Otherwise returns `null`.

All `<img>` tags that bind to LLM/FAL-derived URLs (`ResultProfile`, `CharacterCard`, `SynopsisHero`, etc.) must use `safeImageUrl()`; if it returns `null`, the image is omitted (no broken icon, no fallback to placeholder text leaking the URL).

**Acceptance criteria:**
- AC-FE-IMG-1: `safeImageUrl("javascript:alert(1)")` → `null`.
- AC-FE-IMG-2: `safeImageUrl("https://fal.media/x.png")` → unchanged.
- AC-FE-IMG-3: `safeImageUrl("https://evil.example.com/x.png")` → `null`.
- AC-FE-IMG-4: `safeImageUrl(undefined)` → `null` (no throw).
- AC-FE-IMG-5: Components rendering profile/character images use `safeImageUrl`; no `<img src={raw}>` for LLM-derived URLs.

#### 9.7.3 Rate-Limit Configuration Validation

`RateLimitConfig` rejects misconfiguration at app startup so a typo cannot silently disable rate limiting or lock the app out of itself.

**Acceptance criteria:**
- AC-RL-CONFIG-1: `capacity = 0` → Pydantic `ValidationError` at `Settings()` construction.
- AC-RL-CONFIG-2: `capacity = -5` → `ValidationError`.
- AC-RL-CONFIG-3: `refill_per_second = 0` → `ValidationError`.
- AC-RL-CONFIG-4: `refill_per_second = -1.0` → `ValidationError`.
- AC-RL-CONFIG-5: Defaults (`capacity=30`, `refill_per_second=1.0`) validate cleanly.

#### 9.7.4 Per-Session Feedback Throttle

The global rate limiter caps requests per IP, but a single session can still be spammed by one IP submitting feedback repeatedly. A per-`quiz_id` Redis token-bucket caps feedback submissions for one session.

`app/security/rate_limit.py` exposes `consume_feedback_token(quiz_id) -> (allowed: bool, retry_after_s: int)`. Default: `capacity = 3`, `refill_per_second = 1/60` (i.e., one token per minute). Configurable via `security.feedback_rate_limit`.

**Acceptance criteria:**
- AC-FEEDBACK-RL-1: 3 successive POST `/feedback` for the same `quiz_id` → all 204.
- AC-FEEDBACK-RL-2: 4th POST in the same window → `429` with `errorCode: "RATE_LIMITED"` and `Retry-After` header (>=1).
- AC-FEEDBACK-RL-3: Different `quiz_id` from the same IP is **not** affected by another session's bucket.
- AC-FEEDBACK-RL-4: Redis unavailable → fail-open (request allowed); `feedback.rl.fail_open` logged.

#### 9.7.5 Frontend Friendly Error Mapping

The FE must never display raw backend `detail` text for 5xx (other than already-mapped 502/503/504) or for 4xx without a known `errorCode`. Raw text leaks infrastructure cues and is unfriendly.

`normalizeHttpError()` defaults map:
- Any `5xx` → `"Something went wrong on our end. Please try again."` (already-mapped 502/503/504 keep their specific text).
- Any `4xx` without a known `errorCode` and without status-specific override → `"That request couldn't be completed. Please refresh and try again."`

Raw `detail` is preserved on the `ApiError.details` object only in `IS_DEV` (already today).

**Acceptance criteria:**
- AC-FE-ERR-PROD-1: Backend 500 with `detail: "psycopg2.OperationalError: ..."` → user-facing message is the generic 5xx text; raw detail not exposed in `err.message`.
- AC-FE-ERR-PROD-2: Backend 400 with `detail: "Internal: foo"` and no `errorCode` → user-facing message is the generic 4xx text.
- AC-FE-ERR-PROD-3: Backend 503 unchanged ("temporarily busy") — no regression of existing ACs.
- AC-FE-ERR-PROD-4: `IS_DEV=true` → raw detail still attached on `err.details`.

#### 9.7.6 LLM Structured-Output Size Cap

`llm_service.get_structured_response()` enforces a maximum serialized response size before parsing. A pathological model output cannot bloat the agent state, Redis cache, or DB.

Cap is `llm.max_response_bytes` (default `262_144` = 256 KB).

**Acceptance criteria:**
- AC-LLM-SIZE-1: LLM response whose serialized JSON exceeds the cap → `LLMResponseTooLargeError` is raised; `llm.response.too_large` logged with `bytes`, `model`.
- AC-LLM-SIZE-2: Response just under the cap → returned unchanged.
- AC-LLM-SIZE-3: Cap is configurable via `llm.max_response_bytes`.

#### 9.7.7 Cross-Stack E2E Security Tests

`frontend/tests/e2e/security.spec.ts` validates real FE↔BE behaviour with mocked BE responses crafted to attack the FE:
- AC-FE-E2E-SEC-1: BE returns `image_url: "javascript:alert(1)"` in result payload → FE renders the result page **without** the image and **without** an inline `javascript:` URL anywhere in the DOM.
- AC-FE-E2E-SEC-2: BE returns 500 with raw `detail` containing `"OperationalError"` → FE error UI displays the generic 5xx message; the raw detail is **not** present in the rendered DOM.
- AC-FE-E2E-SEC-3: BE returns 429 with `Retry-After: 2` on `/feedback` → FE surfaces a rate-limit message and disables the resubmit button for the retry window.

---

## 10. Observability

### Structured Logging

- Library: `structlog` with `stdlib` bridge
- Processors: timestamp, log level, caller info, redaction, sampling, JSON renderer (prod) / console renderer (dev)
- `LLM_SLOW_MS` env var (default `2000`): LLM calls exceeding this threshold emit a `slow_llm_call` warning
- `trace_id` propagated through agent state and attached to all log events within a quiz session
- Request `X-Trace-ID` / `X-Request-ID` headers echoed in responses and attached to log context

#### Acceptance Criteria — Request ID propagation (AC-OBS-REQID-*)

- AC-OBS-REQID-1: When a request includes a valid `X-Request-ID` header (1–128 chars, `[A-Za-z0-9_.\-]+`), the server uses it as the canonical `trace_id` for the request, binds it to the `structlog` context, and echoes it back in both `X-Request-ID` and `X-Trace-ID` response headers.
- AC-OBS-REQID-2: When a request omits `X-Request-ID` (or supplies an invalid value), the server generates a fresh UUID4 `trace_id`, ignores the invalid client value, and returns it in `X-Request-ID` and `X-Trace-ID`.
- AC-OBS-REQID-3: A single request never produces two different IDs across response headers; `X-Request-ID == X-Trace-ID`.

### Health Endpoints

- `GET /health` — liveness; always `200`
- `GET /readiness` — readiness; pings DB and Redis

### OpenTelemetry

Instrumented libraries (when OTel collector configured):
- `opentelemetry-instrumentation-fastapi`
- `opentelemetry-instrumentation-asyncpg`
- `opentelemetry-instrumentation-redis`
- `opentelemetry-instrumentation-requests`

---

## 11. Error Handling & HTTP Status Codes

### Global Exception Handler

All unhandled exceptions caught by a custom handler → `500` with structured body:
```json
{
  "detail": "An unexpected internal error occurred...",
  "errorCode": "INTERNAL_SERVER_ERROR",
  "traceId": "<uuid>"
}
```

### Complete Status Code Reference

| Code | Condition |
|------|-----------|
| `200` | Health, readiness, config, quiz status, result |
| `201` | `POST /quiz/start` success |
| `202` | `POST /quiz/proceed`, `POST /quiz/next` accepted |
| `204` | `POST /feedback` success |
| `307` | Redirect from `/` to `/docs` (local/dev only) |
| `400` | Invalid Content-Length; bad question/option index; neither answer nor optionIndex; empty answer; missing/malformed Turnstile token |
| `401` | Turnstile verification failed |
| `404` | Quiz session not found; result not found; feedback quiz not found |
| `409` | Stale questionIndex; atomic Redis update conflict after 8 retries |
| `413` | Request body too large |
| `422` | Pydantic validation error (category length, UUID format, rating value, etc.) |
| `500` | Unhandled exception; malformed result/question data in state; Turnstile service internal error; DB save failed for feedback |
| `503` | DB/Redis not ready (dependency injection fails); agent produced no synopsis |
| `504` | `POST /quiz/start` timed out (asyncio.TimeoutError from `first_step_timeout_s`) |

### Structured Error Bodies

All error responses return JSON:
```json
{ "detail": "Human-readable message." }
```
For `413`:
```json
{ "detail": "Request body too large.", "errorCode": "PAYLOAD_TOO_LARGE" }
```

---

## 12. Environment Variables Reference

| Variable | Type | Default | Purpose |
|----------|------|---------|---------|
| `APP_ENVIRONMENT` | str | `local` | Environment name; controls docs gating, HSTS, LG checkpointer |
| `ALLOWED_ORIGINS` | str | `["http://localhost:5173","http://127.0.0.1:5173"]` | CORS allowed origins (JSON array or CSV) |
| `MAX_REQUEST_BODY_BYTES` | int | `262144` | Request body size cap (bytes) |
| `READINESS_PROBE_TIMEOUT_S` | float | `2.0` | Timeout for each dependency check in `/readiness` |
| `DATABASE_URL` | str | — | Full async DB URL (overrides parts below) |
| `DATABASE_USER` | str | `postgres` | DB username |
| `DATABASE_PASSWORD` | str | `postgres` | DB password |
| `DATABASE_HOST` | str | `localhost` | DB hostname |
| `DATABASE_PORT` | int | `5432` | DB port |
| `DATABASE_DB_NAME` | str | `quiz` | DB name |
| `REDIS_URL` | str | `redis://localhost:6379/0` | Redis URL (overrides parts below) |
| `REDIS_HOST` | str | `localhost` | Redis hostname |
| `REDIS_PORT` | int | `6379` | Redis port |
| `REDIS_DB` | int | `0` | Redis database number |
| `ENABLE_TURNSTILE` | bool | `true` | Enable Cloudflare Turnstile verification |
| `TURNSTILE_SITE_KEY` | str | — | Cloudflare Turnstile site key |
| `TURNSTILE_SECRET_KEY` | str | — | Cloudflare Turnstile secret key |
| `APP_CONFIG_PATH` | str | `appconfig.local.yaml` | Path to YAML file served by `/config` endpoint |
| `APP_CONFIG_LOCAL_PATH` | str | `backend/appconfig.local.yaml` | Path to YAML file for backend settings |
| `CONFIG_CACHE_SECONDS` | int | `60` | `Cache-Control: max-age` for `/config` |
| `CONFIG_MAX_BYTES` | int | `1048576` | Max YAML file size for `/config` |
| `LLM_SLOW_MS` | int | `2000` | Threshold (ms) for slow LLM warning log |
| `GEMINI_API_KEY` | str | — | Google Gemini API key (via LiteLLM) |
| `OPENAI_API_KEY` | str | — | OpenAI API key (via LiteLLM) |
| `USE_MEMORY_SAVER` | `1`/`0` | `0` | Force `InMemorySaver` for LangGraph (local/dev) |
| `LANGGRAPH_REDIS_TTL_MIN` | int | `60` | TTL in minutes for LangGraph Redis checkpoints |

---

## 12a. Database Schema (from `db/init/init.sql`)

The init script runs idempotently — all statements use `IF NOT EXISTS` or `ON CONFLICT DO NOTHING`.

### Extensions

```sql
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";  -- uuid_generate_v4()
CREATE EXTENSION IF NOT EXISTS "vector";     -- pgvector VECTOR type
```

### `characters` table

```sql
CREATE TABLE IF NOT EXISTS characters (
    id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name              TEXT UNIQUE NOT NULL CHECK (name <> ''),
    short_description TEXT NOT NULL CHECK (short_description <> ''),
    profile_text      TEXT NOT NULL CHECK (profile_text <> ''),
    profile_picture   BYTEA,
    judge_quality_score SMALLINT CHECK (judge_quality_score BETWEEN 1 AND 10),
    judge_feedback    TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_characters_name ON characters(name);
```

### `session_history` table

```sql
CREATE TYPE IF NOT EXISTS user_sentiment_enum AS ENUM ('POSITIVE', 'NEGATIVE', 'NONE');

CREATE TABLE IF NOT EXISTS session_history (
    session_id          UUID PRIMARY KEY,
    category            TEXT NOT NULL CHECK (category <> ''),
    category_synopsis   JSONB NOT NULL,
    synopsis_embedding  VECTOR(384),            -- nullable; IVFFlat cosine index
    agent_plan          JSONB,
    session_transcript  JSONB NOT NULL,
    character_set       JSONB NOT NULL DEFAULT '[]'::jsonb,  -- GIN index
    final_result        JSONB,
    judge_plan_score    SMALLINT CHECK (judge_plan_score BETWEEN 1 AND 10),
    judge_plan_feedback TEXT,
    user_sentiment      user_sentiment_enum,
    user_feedback_text  TEXT,
    is_completed        BOOLEAN NOT NULL DEFAULT FALSE,
    completed_at        TIMESTAMPTZ,
    qa_history          JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_session_history_category  ON session_history(category);
CREATE INDEX IF NOT EXISTS idx_session_history_character_set ON session_history USING GIN (character_set);
-- IVFFlat vector index created separately (requires non-empty data):
-- CREATE INDEX IF NOT EXISTS idx_synopsis_embedding ON session_history
--   USING ivfflat (synopsis_embedding vector_cosine_ops) WITH (lists=100);
```

### `character_session_map` association table

```sql
CREATE TABLE IF NOT EXISTS character_session_map (
    character_id UUID REFERENCES characters(id) ON DELETE CASCADE,
    session_id   UUID REFERENCES session_history(session_id) ON DELETE CASCADE,
    PRIMARY KEY (character_id, session_id)
);
```

### `session_questions` table

```sql
CREATE TABLE IF NOT EXISTS session_questions (
    session_id          UUID PRIMARY KEY REFERENCES session_history(session_id) ON DELETE CASCADE,
    baseline_questions  JSONB,    -- GIN index
    adaptive_questions  JSONB,    -- GIN index
    properties          JSONB,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_session_questions_baseline
    ON session_questions USING GIN (baseline_questions);
CREATE INDEX IF NOT EXISTS idx_session_questions_adaptive
    ON session_questions USING GIN (adaptive_questions);
```

### Triggers

Auto-update `last_updated_at` on any UPDATE:
```sql
CREATE OR REPLACE FUNCTION update_last_updated_at()
RETURNS TRIGGER AS $$
BEGIN NEW.last_updated_at = now(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_characters_updated BEFORE UPDATE ON characters
    FOR EACH ROW EXECUTE FUNCTION update_last_updated_at();
CREATE TRIGGER trg_session_history_updated BEFORE UPDATE ON session_history
    FOR EACH ROW EXECUTE FUNCTION update_last_updated_at();
CREATE TRIGGER trg_session_questions_updated BEFORE UPDATE ON session_questions
    FOR EACH ROW EXECUTE FUNCTION update_last_updated_at();
```

---

## 12b. Background Task & LangGraph Execution Model

### Background task pattern

When `/quiz/proceed` or `/quiz/next` triggers an agent run, a FastAPI `BackgroundTask` is added to the response:

```python
background_tasks.add_task(run_agent_in_background, state, redis_client, agent_graph)
```

`run_agent_in_background` runs AFTER the HTTP response is returned to the client. It:
1. Streams `agent_graph.astream(...)` with `{"configurable": {"thread_id": session_id}}` config
2. On completion, reads final state via `agent_graph.aget_state(...)`
3. Saves final state to Redis (`save_quiz_state`)
4. Persists questions/results to PostgreSQL

### DB access in background tasks

Background tasks run outside FastAPI's DI system. They manually drive the `get_db_session` async generator:

```python
gen = get_db_session()
db_session = await gen.__anext__()
try:
    # ... DB operations ...
finally:
    await gen.aclose()
```

### LangGraph checkpointing

- **Production (Redis available):** `AsyncRedisSaver` from `langgraph-checkpoint-redis`
  - `LANGGRAPH_REDIS_TTL_MIN` env controls checkpoint TTL
  - Thread ID = `session_id` (UUID string)
- **Local/dev/test fallback:** `InMemorySaver` when Redis unavailable at startup or `USE_MEMORY_SAVER=1`
- **Checkpoint graph shutdown:** `aclose_agent_graph` properly closes the saver + Redis connection manager

### Agent run per request

Each background run advances the graph from its last checkpoint. The graph is NOT a long-running async task — it runs to completion (to `assemble_and_finish → END`) each time. The state is checkpointed to Redis so the next run resumes from the correct position.

---

## 13. Dependency Stack

| Package | Version Constraint | Role |
|---------|-------------------|------|
| `fastapi` | `>=0.120` | ASGI web framework |
| `uvicorn[standard]` | `>=0.35` | ASGI server |
| `pydantic` | `>=2.11.7` | Data validation (v2 API) |
| `langgraph` | `>=1.1.0` | Agent orchestration graph |
| `langgraph-checkpoint-redis` | `>=0.1.1` | Redis checkpoint saver for LangGraph |
| `langchain-core` | `>=1.0.0` | BaseMessage, tool primitives |
| `litellm` | `>=1.83.7` | LLM provider abstraction |
| `openai` | `>=2.8.0` | OpenAI Responses API |
| `sqlalchemy` | `>=2.0.42` | Async ORM |
| `asyncpg` | `>=0.29` | PostgreSQL async driver |
| `pgvector` | `>=0.4.1` | pgvector extension type |
| `redis` | `>=6.4.0` | Async Redis client + hiredis |
| `structlog` | `>=25.4.0` | Structured logging |
| `httpx` | `>=0.28` | Async HTTP client (Turnstile, external) |
| `python-dotenv` | `>=1.1.1` | `.env` file loading |
| `azure-identity` | `>=1.23.1` | Azure credential provider |
| `azure-appconfiguration-provider` | `>=2.1.0` | Azure App Config (stub) |
| `opentelemetry-sdk` | various | OTel tracing |
| `opentelemetry-instrumentation-fastapi` | various | Auto-instrument FastAPI |
| `opentelemetry-instrumentation-asyncpg` | various | Auto-instrument asyncpg |
| `opentelemetry-instrumentation-redis` | various | Auto-instrument Redis |

**Test dependencies:**
| Package | Role |
|---------|------|
| `pytest` + `pytest-asyncio` | Async test runner |
| `fakeredis` | In-memory Redis mock |
| `aiosqlite` | SQLite async (unit test DB) |
| `httpx` | ASGI test client |
| `pytest-cov` | Coverage reporting |

---

## 14. Acceptance Criteria & Test Matrix

### 14.1 Functional Acceptance Criteria

_(Full list of ACs from §4–§8 consolidated here for test derivation)_

**Health:**
- AC-HEALTH-1 through AC-HEALTH-6 (see §4.1)

**Config:**
- AC-CONFIG-1 through AC-CONFIG-5 (see §4.2)

**Quiz Start:**
- AC-START-1 through AC-START-10 (see §4.3)

**Quiz Proceed:**
- AC-PROCEED-1 through AC-PROCEED-4 (see §4.3)

**Quiz Next:**
- AC-NEXT-1 through AC-NEXT-8 (see §4.3)

**Quiz Status:**
- AC-STATUS-1 through AC-STATUS-6 (see §4.3)

**Results:**
- AC-RESULT-1 through AC-RESULT-3 (see §4.4)

**Feedback:**
- AC-FEEDBACK-1 through AC-FEEDBACK-7 (see §4.5)

**Agent nodes:**
- AC-AGENT-BOOTSTRAP-1: `analyze_topic` called with raw category; result stored in state; safe defaults used on exception
- AC-AGENT-BOOTSTRAP-2: `plan_quiz` tool called once per session; bootstrap node is idempotent (skips if synopsis already in state)
- AC-AGENT-TOPIC-MEDIA-1: `_handle_media_topic` does **not** append `" Characters"` when `canonical_for(raw)` returns a non-empty list. Example: `"Hunger Games District"` (alias of canonical set `"Hunger Games Districts"`) → `normalized_category="Hunger Games District"`, `outcome_kind="characters"`, `is_media=True`.
- AC-AGENT-TOPIC-MEDIA-2: `_handle_media_topic` does **not** append `" Characters"` when the trailing token of the raw input is a known non-character subgroup noun (`district`, `districts`, `house`, `houses`, `faction`, `factions`, `team`, `teams`, `club`, `clubs`, `guild`, `guilds`, `clan`, `clans`, `tribe`, `tribes`, `kingdom`, `kingdoms`, `realm`, `realms`, `region`, `regions`, `planet`, `planets`, `world`, `worlds`, `nation`, `nations`, `side`, `sides`, `family`, `families`, `school`, `schools`, `race`, `races`, `class`, `classes`, `species`, `role`, `roles`).
- AC-AGENT-TOPIC-MEDIA-3: `_handle_media_topic` continues to append `" Characters"` when neither AC-AGENT-TOPIC-MEDIA-1 nor AC-AGENT-TOPIC-MEDIA-2 applies (e.g., `"Batman Movie"` → `"Batman Movie Characters"`).
- AC-AGENT-CHARS-1: Characters generated count within `[min_characters(4), max_characters(6)]` by default
- AC-AGENT-CHARS-2: Concurrency bounded by `min(4, len(archetypes))` unless explicitly configured
- AC-AGENT-CHARS-3: Archetype list repaired if empty, too small, or too large via `_repair_archetypes_if_needed`
- AC-AGENT-CHARS-4: Fallback archetypes `["The Analyst","The Dreamer","The Realist"]` used if repair also fails
- AC-AGENT-BASELINE-1: `generate_baseline_questions` called once per session (idempotent; checks `baseline_ready`)
- AC-AGENT-BASELINE-2: Exactly `baseline_questions_n (5)` questions generated; truncated if tool returns more
- AC-AGENT-BASELINE-3: Each question has ≤ `max_options_m (4)` options; questions deduplicated by text
- AC-AGENT-DECIDE-1: `FINISH_NOW` suppressed when `answered < min_questions_before_early_finish (6)`
- AC-AGENT-DECIDE-2: `FINISH_NOW` forced (pre-tool) when `answered >= max_total_questions (20)`
- AC-AGENT-DECIDE-3: `FINISH_NOW` suppressed when `confidence < early_finish_confidence (0.9)`
- AC-AGENT-DECIDE-4: Confidence normalised: values > 1.0 divided by 100
- AC-AGENT-DECIDE-5: Decision tool skipped entirely when `answered < baseline_count`; returns `should_finalize=False`
- AC-AGENT-RESULT-1: `final_result` contains `title`, `description`; `imageUrl` is null
- AC-AGENT-RESULT-2: `is_completed=True` written to DB after finalisation
- AC-AGENT-RESULT-3: Fallback `FinalResult(title="Result Error", description="...")` used if `write_final_user_profile` throws

**Adaptive Research & Model Tiering (§7.7):**
- AC-AGENT-KNOW-1: `classify_topic_knowledge` short-circuits with `is_well_known=True, knowledge_score=1.0` when `canonical_for(category)` returns a non-empty list (no LLM call).
- AC-AGENT-KNOW-2: `classify_topic_knowledge` short-circuits with `is_well_known=True` when `topic_analysis.creativity_mode == "factual"` AND `topic_analysis.domain in {"frameworks_types_systems","serious_professions_profiles"}` (no LLM call).
- AC-AGENT-KNOW-3: When neither short-circuit hits, the classifier calls `topic_knowledge_classifier` (Lite tier) and returns a `TopicKnowledgeAssessment`; on LLM exception or invalid output, returns `is_well_known=True, knowledge_score=1.0` (fail-open: skip research).
- AC-AGENT-KNOW-4: `state["topic_knowledge"]` is set by the bootstrap node before `plan_quiz` runs.
- AC-AGENT-KNOW-5: Canonical category data is layered from a built-in reviewed catalog plus config-defined overrides (`settings.canonical_sets` / `appconfig.local.yaml`); later layers override earlier layers shallowly.
- AC-AGENT-KNOW-6: The built-in reviewed catalog is assembled in 10 themed expansion passes so additions remain auditable by domain (frameworks, fantasy factions, geography, science, sports, etc.).
- AC-AGENT-KNOW-7: The compiled canonical catalog exposes at least 200 canonical sets and at least 2,000 canonical outcome names in aggregate.
- AC-AGENT-KNOW-8: The expanded catalog includes precise lookup support for additional bounded taxonomies such as Pokémon Types, Ilvermorny Houses, Divergent Factions, Avatar Nations, U.S. States, U.S. state capitals, periodic-table elements, astronomy sets, and major North American league teams.
- AC-AGENT-RESEARCH-1: `gather_topic_research` is **not called** when `topic_knowledge.is_well_known is True`.
- AC-AGENT-RESEARCH-2: `gather_topic_research` is **not called** when `settings.retrieval.policy == "off"` or `settings.retrieval.allow_web is False`, regardless of classifier output.
- AC-AGENT-RESEARCH-3: When called and the configured provider is `gemini/*`, the call uses LiteLLM with `tools=[{"googleSearch": {}}]`; the resulting text is stored under `state["research_context"]` and `state["research_provider"] == "gemini_grounding"`.
- AC-AGENT-RESEARCH-4: When the Gemini grounding call raises or returns an empty string, the OpenAI Responses `web_search` tool is invoked as a fallback; on success `state["research_provider"] == "openai_web_search"`.
- AC-AGENT-RESEARCH-5: When both Gemini grounding and OpenAI web search fail and `allow_wikipedia=True`, `wikipedia_search` is invoked once; on success `state["research_provider"] == "wikipedia"`.
- AC-AGENT-RESEARCH-6: When all providers fail, `research_used=False`, `research_provider="none"`, `research_context=""`, and downstream nodes proceed without raising.
- AC-AGENT-RESEARCH-7: `gather_topic_research` consumes at most one retrieval slot per provider attempt and never exceeds `settings.retrieval.max_calls_per_run`.
- AC-AGENT-RESEARCH-8: Total wall-time spent in `gather_topic_research` is bounded by `settings.retrieval.research_latency_budget_s`; on overrun, partial output is returned and the node continues.
- AC-AGENT-RESEARCH-9: `research_context` is stripped of control chars and truncated to ≤ 4096 characters before storage.
- AC-AGENT-TIER-1: Per-tool model strings come from `settings.llm_tools[<tool>].model` and **never** from Python literals.
- AC-AGENT-TIER-2: For `profile_batch_writer` and `question_generator`, the resolved model is `model_unknown` (Pro / 3.x tier) when `state["topic_knowledge"]["is_well_known"] is False`, else `model` (Flash tier).
- AC-AGENT-TIER-3: When `model_unknown` is missing from config, the resolver falls back to `model` (no exception, logs a warning once per process).
- AC-AGENT-PROMPT-1: When `state["topic_knowledge"]["is_well_known"] is True`, prompts that have a `*_speed` variant resolve to that variant; otherwise the default (or `*_depth`) variant is used.
- AC-AGENT-PROMPT-2: When `research_context` is empty, prompts referencing `{search_context}` or `{character_contexts}` substitute the empty string and the model continues to produce valid output.

**Image Generation (FAL — §7.8):**
- AC-IMG-1: `FalImageClient.generate` returns `None` (never raises) when `fal_client` raises any exception, when `asyncio.wait_for` times out, or when the response lacks an `images[0].url`.
- AC-IMG-2: When `image_gen.enabled is False`, `FalImageClient.generate` short-circuits to `None` without invoking `fal_client`.
- AC-IMG-3: `build_character_image_prompt` produces a prompt string that contains the character's `short_description` substring AND the configured `style_suffix`, and a `negative_prompt` equal to the configured default. It MUST NOT include the verbatim `category` token if `analysis.is_media is True` (avoids IP names); a descriptive prefix is used instead.
- AC-IMG-4: `build_synopsis_image_prompt` produces an abstract / non-character description and never embeds `synopsis.title` verbatim when `analysis.is_media is True`.
- AC-IMG-5: `build_result_image_prompt` prefers the matched character's `short_description` from `character_set`; falls back to `result.title`/`description` if no match.
- AC-IMG-6: `generate_character_images` runs N FAL calls bounded by `asyncio.Semaphore(image_gen.concurrency)` and returns a `dict[name, Optional[str]]` for **every** input character (failures map to `None`).
- AC-IMG-7: `generate_character_images` UPDATEs `characters.image_url` only when current value `IS NULL` (no overwrite of curated images) and refreshes the matching `session_history.character_set[*].image_url` for the session.
- AC-IMG-8: `generate_synopsis_image` updates `session_history.category_synopsis` JSONB with `image_url` key without dropping other JSONB keys.
- AC-IMG-9: `generate_result_image` updates `session_history.final_result` JSONB with `image_url` key; safe no-op when `final_result IS NULL`.
- AC-IMG-10: All three pipeline functions are scheduled via `BackgroundTasks` from the route handlers; the user-visible response payload SHALL be returned with `image_url` either populated (cache hit) or `None` (background fetch in flight) — the response never blocks waiting for FAL.
- AC-IMG-ASPECT-1: `generate_synopsis_image` SHALL pass `image_size={"width":1024,"height":576}` to `FalImageClient.generate` so the synopsis hero is generated in 16:9 landscape, matching the frontend's landscape hero card and preventing top/bottom cropping.
- AC-IMG-ASPECT-2: `generate_result_image` SHALL pass `image_size={"width":1024,"height":576}` to `FalImageClient.generate` for the same reason on the results page.

**Persistence:**
- AC-DB-1: Characters upserted by name; duplicate name does not create duplicate row
- AC-DB-2: Session row exists after `/quiz/start`
- AC-DB-3: `character_session_map` links all characters to session
- AC-DB-4: `qa_history` written after each `/quiz/next`
- AC-DB-5: `is_completed=TRUE`, `completed_at` set after finalisation
- AC-DB-6: `user_sentiment` set correctly after feedback

**Redis:**
- AC-REDIS-1: Session state saved with TTL 3600s after `/quiz/start`
- AC-REDIS-2: `ready_for_questions=True` after `/quiz/proceed`
- AC-REDIS-3: `quiz_history` appended atomically after `/quiz/next`
- AC-REDIS-4: State returns `None` on Redis miss (fail-open, no 500)
- AC-REDIS-5: `update_quiz_state_atomically` retries up to 8 times on `WatchError`; returns `None` (no exception raised) after exhaustion

---

### 14.2 Security Acceptance Criteria

- AC-SEC-TURNSTILE-1 through AC-SEC-TURNSTILE-7 (see §9.1)
- AC-SEC-CORS-1 through AC-SEC-CORS-6 (see §9.2)
- AC-SEC-HEADERS-1 through AC-SEC-HEADERS-3 (see §9.3)
- AC-SEC-BODY-1 through AC-SEC-BODY-4 (see §9.4)
- AC-SEC-DOCS-1 through AC-SEC-DOCS-2 (see §9.5)
- AC-SEC-LOGS-1 through AC-SEC-LOGS-3 (see §9.6)

**Additional:**
- AC-SEC-INJECT-1: SQL injection in `category` field → sanitised by ORM parameterisation; no DB error
- AC-SEC-INJECT-2: XSS payload in `category` → stored and returned as plain text; no HTML execution
- AC-SEC-INJECT-3: Oversized `answer` field (> 2048 chars) → `400` not `500`
- AC-SEC-INJECT-4: Null bytes in request body → `422` validation error

---

### 14.3 Performance Acceptance Criteria

- AC-PERF-1: `GET /health` responds within 50ms at p99
- AC-PERF-2: `GET /readiness` responds within `READINESS_PROBE_TIMEOUT_S + 100ms`
- AC-PERF-3: `GET /config` responds within 100ms (YAML loaded, cached by `lru_cache`)
- AC-PERF-4: `GET /quiz/status/{id}` (cache hit) responds within 200ms
- AC-PERF-5: `POST /quiz/next` request handling (excluding background LLM work) within 500ms
- AC-PERF-6: Redis pool does not exhaust at 50 concurrent quiz status polls
- AC-PERF-7: DB pool (`db_pool_size + db_max_overflow`) handles 50 concurrent operations without timeout
- AC-PERF-8: For `bucket: known` topics in the eval set, bootstrap node p95 latency does not regress by > 25 % vs the captured baseline iteration.
- AC-PERF-9: For `bucket: known` topics, `research_used` is `False` for ≥ 95 % of runs; for `bucket: fringe` topics, `research_used` is `True` for ≥ 80 % of runs (when `retrieval.policy="adaptive"` and `allow_web=True`).
- AC-PERF-10: Total LLM call count for a `/quiz/start` on a `bucket: known` topic does not exceed `(1 plan_quiz + 1 character batch) = 2` outside of canonical/repair edge cases.

---

### 14.4 Edge & Resilience Criteria

- AC-EDGE-1: Calling `/quiz/proceed` twice for same session → second call also returns `202` (idempotent)
- AC-EDGE-2: Calling `/quiz/next` with same `questionIndex` twice → second call returns `409`
- AC-EDGE-3: Redis unavailable at `/quiz/status` poll → `404` or `503` (not `500` crash)
- AC-EDGE-4: DB unavailable at `/quiz/start` → `503` with appropriate message
- AC-EDGE-5: LangGraph node raises exception → `error_count` incremented; agent does not loop infinitely
- AC-EDGE-6: LLM call times out → `StructuredOutputError` raised; handled gracefully by node
- AC-EDGE-7: `category` containing only Unicode whitespace → `422`
- AC-EDGE-8: `quizId` that is valid UUID but not in DB → `404` (not `500`)
- AC-EDGE-9: Session state in Redis is corrupted (invalid JSON) → `get_quiz_state` returns `None`; endpoint returns `404`
- AC-EDGE-10: `generate_characters` concurrency slot exhausted → waits in queue; does not deadlock

---

### 14.5 Test File Map

Tests live in `backend/tests/`. Each acceptance criterion should map to at least one test.

| Test Category | Directory | Scope | Key Files |
|---------------|-----------|-------|-----------|
| Unit — Agent | `tests/unit/agent/` | Graph nodes, tools, state, prompts | `test_graph.py`, `test_canonical_sets.py`, `tools/test_*.py` |
| Unit — API | `tests/unit/api/` | Endpoint handlers + dependency injection (ASGI, no real DB/Redis) | `endpoints/test_quiz.py`, `test_feedback.py`, `test_results.py`, `test_config.py` |
| Unit — Models | `tests/unit/models/` | Pydantic schema validation, ORM model construction | `test_api.py`, `test_database.py` |
| Unit — Services | `tests/unit/services/` | Repository methods, LLMService extraction, CacheRepository | `test_database_service.py`, `test_llm_service.py`, `test_redis_cache.py` |
| Integration | `tests/integration/` | Full request→DB→Redis flow; real SQLite or postgres | `test_quiz_start.py`, `test_quiz_proceed.py`, `test_quiz_next.py`, `test_quiz_status.py`, `test_feedback_endpoint.py`, `test_results_endpoint.py`, `test_health.py` |
| Security | `tests/security/` | CORS headers, Turnstile behaviour, docs gating, body size, log redaction, input validation | `test_cors.py`, `test_turnstile.py`, `test_docs_security.py`, `test_request_size.py`, `test_headers.py`, `test_input_validation.py` |
| Performance | `tests/performance/` | Response times, concurrency, pool exhaustion | `test_response_times.py`, `test_concurrency.py` |
| Smoke | `tests/smoke/` | Minimal end-to-end: start→proceed→poll→finish | `test_smoke.py` |
| Reliability | `tests/reliability/` | App lifecycle, CORS parser variants, Redis failure modes | `test_app_lifecycle.py` |
| Observability | `tests/observability/` | Log fields present, trace_id propagated, slow-LLM log | `test_logging.py` |

**TDD Process (mandatory for `backend/` changes):**

1. **Update this spec** — add or modify acceptance criteria in the relevant section
2. **Write failing tests** — one test per AC, covering: happy path, error paths, edge cases, security
3. **Run test suite** — confirm tests fail for the right reasons
4. **Implement code changes** — minimum code to make tests pass
5. **Re-run full suite** — all previously passing tests must still pass
6. **Update `backend/README.md`** — if public API surface, config, or behaviour changed

```
Spec change → failing tests → code → green tests → README update
```

---

## 15. Production Hardening (§15)

This section captures hardening rules added during the production-readiness audit on 2026-04-27. Each rule has acceptance criteria the implementation and tests are validated against.

### 15.1 Distributed Rate Limiting (`AC-RL-*`)

A Redis-backed token-bucket rate limiter middleware guards all HTTP endpoints except a small allowlist (health, readiness, docs, openapi, root redirect). The limiter uses a single atomic Lua script per request, fails open on Redis errors, and emits structured logs on rejection.

- AC-RL-1: When the bucket has tokens → request proceeds, response carries `X-RateLimit-Remaining` and `X-RateLimit-Limit` headers.
- AC-RL-2: When the bucket is empty → response is `429 Too Many Requests` with body `{"detail": "...", "errorCode": "RATE_LIMITED"}` and headers `Retry-After` (integer seconds) and `X-RateLimit-Remaining: 0`.
- AC-RL-3: Allowlisted paths (`/health`, `/readiness`, `/docs`, `/redoc`, `/openapi.json`, `/`) are never rate-limited.
- AC-RL-4: When Redis is unreachable, the limiter logs `rate_limit.fail_open` at `warning` and allows the request (fail-open, never blocks user traffic on infrastructure error).
- AC-RL-5: Bucket key is derived from request **client IP** (X-Forwarded-For first hop if present, else `request.client.host`) plus a coarse route bucket (path prefix), so bursts on `/api/quiz/start` do not consume the budget for `/api/config`.
- AC-RL-6: Rate-limit configuration (`capacity`, `refill_per_second`, `enabled`) is sourced from `settings.security.rate_limit` with safe defaults (capacity=30, refill_per_second=1.0).
- AC-RL-7: Disabled (`enabled=false`) → middleware is a no-op; never touches Redis.

### 15.2 Trusted-Host Validation (`AC-HOST-*`)

A `TrustedHostMiddleware` rejects requests whose `Host` header is not in `settings.security.trusted_hosts`. Default in non-production envs allows `*` (development friendly); in `production`/`staging` envs the default is restrictive (`localhost`, `127.0.0.1`).

- AC-HOST-1: Production env + Host header not in allowlist → response is `400 Bad Request` (FastAPI/Starlette default) and event `request.untrusted_host` logged at `warning`.
- AC-HOST-2: Local/dev env → wildcard host accepted by default.
- AC-HOST-3: Allowlist supports a single trailing-wildcard subdomain (e.g. `*.azurecontainerapps.io`).

### 15.3 Input Validation Hardening (`AC-IN-*`)

`StartQuizRequest.category` is hardened against control characters, RTL/LTR override exploits, null bytes, and oversized inputs.

- AC-IN-1: Category containing any C0/C1 control char (codepoints 0–31 except `\t` is also rejected, and 127–159) → `422` with field-level error.
- AC-IN-2: Category containing Unicode bidi-override codepoints (U+202A..U+202E, U+2066..U+2069) → `422`.
- AC-IN-3: Category containing a NUL byte (`\x00`) → `422`.
- AC-IN-4: Category whose UTF-8 byte length exceeds 400 bytes → `422` (covers the common "long string of multi-byte glyphs" DoS vector even when char length ≤ 100).
- AC-IN-5: Category that is whitespace-only or becomes empty after stripping → `422`.
- AC-IN-6: Leading/trailing whitespace and runs of internal whitespace are normalized to a single space before validation, but the original semantics are preserved.

### 15.4 Single-Flight Session Lock (`AC-LOCK-*`)

`/api/quiz/proceed` and `/api/quiz/next` acquire a short-lived Redis lock (`SET NX EX`) keyed by `quiz_id`, preventing two concurrent answers for the same session from racing on `last_served_index` or `quiz_history`.

- AC-LOCK-1: Lock not held → handler proceeds normally and releases on completion (best-effort `DEL`).
- AC-LOCK-2: Lock held by another request → response is `409 Conflict` with body `{"detail": "...", "errorCode": "SESSION_BUSY"}`. No DB or Redis mutations occur.
- AC-LOCK-3: Lock TTL defaults to 10 seconds; configurable via `settings.security.session_lock_ttl_s`.
- AC-LOCK-4: When Redis is unreachable, the lock is treated as **not held** (fail-open) and the handler proceeds, with a `session_lock.fail_open` warning log.
- AC-LOCK-5: Lock release uses a token-matched delete (Lua) so that an expired lock acquired by a second request is not deleted by the first.

### 15.5 PII Log Scrubbing (`AC-LOGS-*`)

A structlog processor masks email addresses and credit-card-like 13–19 digit runs in any string value emitted by application loggers, regardless of key. This is additive to the existing `[REDACTED]` rules for known sensitive keys.

- AC-LOGS-PII-1: A log event with value containing `user@example.com` → emitted value contains `user@***` (local part preserved up to 4 chars, domain replaced with `***`).
- AC-LOGS-PII-2: A log event with value containing a 16-digit number `4111111111111111` → emitted value contains `****1111` (last-4 only).
- AC-LOGS-PII-3: Non-string values (ints, dicts, lists) are recursively scanned; nested string values inside dicts/lists are scrubbed.
- AC-LOGS-PII-4: The processor is idempotent — running it twice on the same event does not double-mask.

### 15.6 Bounded Lookups (`AC-IDS-*`)

Repository helpers that accept a list of identifiers cap the list at a hard maximum (default 100) to prevent unbounded `WHERE id IN (...)` queries.

- AC-IDS-1: `CharacterRepository.get_many_by_ids` with > 100 ids → raises `ValueError("ids list exceeds maximum (100)")` before executing any SQL.
- AC-IDS-2: With ≤ 100 ids → executes one query, returns matched rows.
- AC-IDS-3: Empty list → returns `[]` without executing SQL.

---

## 16. Reliability Hardening (`AC-RELY-*`)

This section captures resilience rules added during the production-readiness audit on 2026-04-27 (Phase 8 — reliability). Each rule has acceptance criteria the implementation and tests are validated against.

### 16.1 LLM Transient-Error Retry (`AC-LLM-RETRY-*`)

`LLMService.get_structured_response` wraps the LiteLLM call (`litellm.responses` via `asyncio.to_thread`) in a bounded retry loop. The retry helper lives in `app/services/retry.py` and is reused by FAL.

- AC-LLM-RETRY-1: Transient errors (`asyncio.TimeoutError`, `litellm.Timeout`, `litellm.APIConnectionError`, `litellm.RateLimitError`, `litellm.InternalServerError`, `litellm.ServiceUnavailableError`) are retried up to **3 attempts total** (1 initial + 2 retries) with exponential backoff `base_ms * 2 ** attempt` plus uniform jitter `[0, base_ms)`.
- AC-LLM-RETRY-2: Non-transient errors (`litellm.AuthenticationError`, `litellm.BadRequestError`, `StructuredOutputError`, `ValidationError`, generic `ValueError`, etc.) are **never retried**; the first exception is re-raised.
- AC-LLM-RETRY-3: A successful retry emits structured log `llm.structured.retry_succeeded` with `attempts`, `tool`, `model`. Final failure emits `llm.structured.retries_exhausted` with the same fields plus `error`.
- AC-LLM-RETRY-4: Retry parameters (`max_attempts`, `base_ms`, `cap_ms`) are sourced from `settings.llm.retry` with safe defaults (3, 200, 2000); setting `max_attempts=1` disables retry entirely (back to current behaviour).
- AC-LLM-RETRY-5: Total elapsed retry time MUST NOT exceed `(max_attempts) * cap_ms + per-call timeout * max_attempts`; the helper aborts further retries when the deadline would exceed twice the per-call timeout.

### 16.2 FAL Image Transient-Error Retry (`AC-IMG-RETRY-*`)

`FalImageClient.generate` retries the same network class of errors using the shared retry helper. The fail-open contract (return `None`, never raise) is preserved.

- AC-IMG-RETRY-1: Transient errors raised by `fal_client.subscribe_async` (any subclass of `asyncio.TimeoutError`, `ConnectionError`, `OSError`, or any exception whose stringified message matches `/429|rate.?limit|503|502|504|timeout|connection/i`) are retried up to **2 attempts total** (1 initial + 1 retry) with exponential backoff matching §16.1.
- AC-IMG-RETRY-2: After the final attempt, `generate()` returns `None` and emits `image.fal.retries_exhausted` at `info` level. It NEVER raises.
- AC-IMG-RETRY-3: Non-transient exceptions (e.g. `ValueError` from prompt validation) bypass retry and immediately return `None` (existing behaviour).
- AC-IMG-RETRY-4: Retry parameters come from `settings.image_gen.retry` with defaults (max_attempts=2, base_ms=200, cap_ms=1500). `max_attempts=1` disables retry.

### 16.3 Session Lock at `/quiz/proceed` (`AC-LOCK-PROCEED-*`)

Extends §15.4 single-flight lock to also cover `/api/quiz/proceed`, which mutates Redis state (`ready_for_questions = True`) and may schedule background work. Without this, two simultaneous proceeds for the same session can both transition state and double-fire the agent.

- AC-LOCK-PROCEED-1: Lock not held → handler proceeds normally and releases on completion via token-matched delete.
- AC-LOCK-PROCEED-2: Lock held by another in-flight `/proceed` or `/next` for the same `quizId` → response is `409 Conflict` with body `{"detail": "...", "errorCode": "SESSION_BUSY"}`. No state mutations occur.
- AC-LOCK-PROCEED-3: When Redis is unreachable, lock is treated as not held (fail-open), `session_lock.fail_open` is logged at warning, and the handler proceeds (mirrors AC-LOCK-4).

### 16.4 Image Pipeline DB Transaction Hygiene (`AC-IMG-TX-*`)

Each `_persist_*` helper in `app/services/image_pipeline.py` MUST explicitly roll back the transaction when an exception is caught, so the underlying `AsyncSession` is returned to the pool in a clean state and partial writes are not silently committed by `__aexit__`.

- AC-IMG-TX-1: When `session.execute(...)` or `session.commit()` raises, the helper catches the exception, calls `await session.rollback()`, logs `image.persist.<kind>.fail`, and never re-raises.
- AC-IMG-TX-2: When `_db_session_ctx` yields `None` (factory unset, e.g. during shutdown), helpers return immediately without touching DB.

### 16.5 Frontend Polling Exponential Backoff (`AC-FE-RELY-POLL-*`)

`quizStore.beginPolling` already clamps any single delay at `MAX_POLL_DELAY_MS=10s` and honours server `Retry-After`. We additionally apply exponential backoff with jitter for **consecutive 5xx / network failures** so the FE doesn't hammer a degraded BE at the floor delay.

- AC-FE-RELY-POLL-1: After a successful poll, the failure-streak counter resets to 0 and the next poll uses `processingPollMs` (no backoff).
- AC-FE-RELY-POLL-2: After N consecutive 5xx or network errors during polling, the next poll delay is `min(MAX_POLL_DELAY_MS, processingPollMs * 2 ** (N-1)) + jitter` where `jitter` is uniform `[0, 250ms)`.
- AC-FE-RELY-POLL-3: A server `Retry-After` header always wins over both the base delay and the backoff (still subject to `MAX_POLL_DELAY_MS` clamp).
- AC-FE-RELY-POLL-4: HTTP `404` (session-gone) breaks the polling loop and surfaces an error; backoff is not applied (terminal state).

### 16.6 Frontend SESSION_BUSY UX (`AC-FE-RELY-LOCK-*`)

The FE recognises `errorCode === "SESSION_BUSY"` (HTTP 409 from `/quiz/next` or `/quiz/proceed`) as a transient retriable condition rather than a hard failure.

- AC-FE-RELY-LOCK-1: A 409 with `errorCode: "SESSION_BUSY"` from `/quiz/next` does NOT transition the store to `error`; the store remains in its current state and exposes a `lastTransientError` field for the UI to render a non-blocking notice.
- AC-FE-RELY-LOCK-2: Any other 409 (no errorCode, or different code) continues to behave as before (terminal error).

---

## 17. Scalability Hardening (`AC-SCALE-*`)

This section captures scalability-focused rules added during the production-readiness audit on 2026-04-27 (Phase 10 — scale). These complement §15/§16 by protecting the process from upstream saturation, runaway concurrency, and unbounded session retention.

### 17.1 Global LLM Concurrency Semaphore (`AC-SCALE-LLM-*`)

Every call into `LLMService.get_structured_response` MUST acquire a process-level `asyncio.Semaphore` before invoking the upstream provider. This bounds the number of concurrent LiteLLM calls (and therefore upstream HTTP connections, memory pressure from JSON parsing, and `asyncio.to_thread` workers) regardless of how many quiz sessions are active. The semaphore is created lazily on first acquire so test fixtures and lifespan-less direct calls work transparently.

- AC-SCALE-LLM-1: When the number of in-flight LLM calls is `< settings.llm.max_concurrency`, a new call proceeds immediately and emits a structured `llm.concurrency.acquired` debug log with `{tool, in_flight, capacity}`.
- AC-SCALE-LLM-2: When the limit is reached, additional callers wait on the semaphore (FIFO) up to `settings.llm.acquire_timeout_s`. If the deadline elapses first, the caller raises `LLMConcurrencyTimeoutError` (mapped to HTTP 503 by API handlers via `agent.execution_error` → graceful failure response). The wait emits `llm.concurrency.wait` at `info` level with `{tool, in_flight, capacity, waited_s}`.
- AC-SCALE-LLM-3: The semaphore MUST be released even when the inner LiteLLM call raises (timeout, validation, oversized payload, etc.). Tests verify `in_flight` returns to 0 after exception paths.
- AC-SCALE-LLM-4: `settings.llm.max_concurrency` defaults to **16** (sized for ~ 4 simultaneous quiz sessions × ~4 parallel character/question generations). `settings.llm.acquire_timeout_s` defaults to **30.0** seconds (slightly less than the per-call LLM timeout so semaphore-pressure errors surface before client request timeouts).
- AC-SCALE-LLM-5: When `max_concurrency <= 0`, configuration validation fails fast at startup with `ValueError`.
- AC-SCALE-LLM-6: A `metrics()` accessor on the semaphore wrapper exposes `{capacity, in_flight, available, total_acquired, total_timeouts}` for observability and tests; never raises.

### 17.2 Graceful Shutdown (`AC-SCALE-SHUTDOWN-*`)

The FastAPI lifespan teardown MUST drain in-flight LLM/agent work before disposing of pools, so partially completed sessions either finish writing state or are explicitly marked failed. This prevents the on-going DB/Redis state writes from racing pool disposal during a SIGTERM.

- AC-SCALE-SHUTDOWN-1: On lifespan exit, the app waits up to `settings.shutdown_grace_s` (default **15.0** seconds) for the LLM concurrency semaphore to reach `in_flight == 0` before continuing teardown. The wait is implemented as a bounded `asyncio.sleep` poll (50ms interval) — never `wait_for(Future, timeout)` against a sentinel that may not be set.
- AC-SCALE-SHUTDOWN-2: If the grace window elapses while in-flight work remains, the app logs `shutdown.in_flight_remaining` at `warning` level with the residual count and proceeds with teardown anyway (best-effort; the orchestrator has already decided to terminate).
- AC-SCALE-SHUTDOWN-3: After the drain window, agent graph close, DB engine dispose, and Redis pool dispose proceed in order with individual `try/except` guards (existing behaviour preserved).
- AC-SCALE-SHUTDOWN-4: `settings.shutdown_grace_s` ≥ 0; a value of `0` disables the drain wait entirely (useful for unit tests).

### 17.3 Session History Retention (`AC-SCALE-RETENTION-*`)

`session_history` rows are unbounded today; over time the table grows large enough that the IVFFlat index degrades and JSONB GIN scans slow down. We add a single explicit retention helper invokable from a scheduled task (cron / Azure Function / `python -m app.scripts.purge_sessions`) — NOT an in-process background loop, which would complicate horizontal scale-out.

---

## 19. Spring 2026 UX Refinement Compatibility (BE-UX26-*)

This round focuses on frontend usability polish and does not introduce new backend endpoints, auth rules, or persistence flows. Backend responsibilities are compatibility and stability of the existing `/config` contract.

- AC-BE-UX26-1: `GET /api/v1/config` response shape remains backward compatible for existing FE consumers; no required keys are removed.
- AC-BE-UX26-2: Existing `quizzical.frontend.theme` token schema remains authoritative; visual refinements are achieved using existing token classes (`colors`, `fonts`, `fontSizes`, `layout.landing`) plus FE-side styling composition.
- AC-BE-UX26-3: Existing `quizzical.frontend.content` fields remain valid; frontend helper/status copy added in this round may use FE fallbacks when backend content keys are absent.
- AC-BE-UX26-4: Error envelope semantics (`detail`, `errorCode`, `traceId`, `details`) remain unchanged to support FE typed error UX.

- AC-SCALE-RETENTION-1: `SessionRepository.purge_older_than(days: int) -> int` deletes session rows whose `created_at < now() - interval 'N days'` AND whose `is_completed = TRUE` AND whose result has been retrievable for > `days/2` (i.e. results are not yanked from under a still-active share link). Returns the count deleted.
- AC-SCALE-RETENTION-2: `purge_older_than(0)` MUST raise `ValueError("days must be >= 1")` to prevent accidental table truncation.
- AC-SCALE-RETENTION-3: Cascading deletes via `character_session_map` and `session_questions` (existing FK ON DELETE CASCADE) clean up associated rows in the same transaction.
- AC-SCALE-RETENTION-4: The helper logs `db.purge.complete` with `{days, deleted_count, duration_ms}` at `info` level.

### 17.4 Server-Timing Per-Segment Breakdown (`AC-SCALE-TIMING-*`)

The current `Server-Timing` header reports only `app;dur=…`. For latency triage in production we add named segments emitted by hot-path handlers. Segments are opt-in via a request-scoped `TimingRecorder` injected through middleware so endpoints simply call `recorder.record("name", duration_ms)`.

- AC-SCALE-TIMING-1: `Server-Timing` response header MUST contain at least `app;dur=…` (existing) plus any additional named segments recorded during the request, joined by commas, e.g. `app;dur=42.3, db;dur=12.7, redis;dur=3.1, llm;dur=18.0`.
- AC-SCALE-TIMING-2: Segment names are restricted to the safe charset `[A-Za-z0-9_-]`; invalid names are silently dropped (no exception, no header injection).
- AC-SCALE-TIMING-3: Duplicate segment names accumulate (sum of durations) so e.g. multiple DB calls report a single `db` total. Order in the header reflects insertion order of first occurrence.
- AC-SCALE-TIMING-4: When no extra segments were recorded, the header reduces to just `app;dur=…` (no trailing comma, no empty value).

---

## 18. Code Quality Hardening (`AC-QUALITY-*`)

This section formalises the code-quality contracts the backend MUST honour. The
goals are uniform error responses, an explicit domain-exception hierarchy,
modern Python typing, and small, focused orchestration functions.

### 18.1 Unified API error envelope

Every error response from the API — whether produced by `HTTPException`,
`RequestValidationError`, an `AppError` subclass, or an unexpected
`Exception` — MUST be serialised using the same `APIError` envelope.

```jsonc
{
  "detail": "Quiz session not found.",   // human-readable summary, kept for FE BC
  "errorCode": "NOT_FOUND",              // stable machine-readable code
  "traceId": "<X-Trace-ID echoed>",      // for log correlation
  "details": null                        // optional structured context (pydantic errors, etc.)
}
```

- AC-QUALITY-ERR-1: Every 4xx and 5xx response from the `/api` surface includes the keys `detail` (string), `errorCode` (string), and `traceId` (string). `details` is optional but, when present, MUST be a JSON-serialisable object/array — never a bare string or number.
- AC-QUALITY-ERR-2: The default mapping from HTTP status to `errorCode` is: `400→BAD_REQUEST`, `401→UNAUTHORIZED`, `403→FORBIDDEN`, `404→NOT_FOUND`, `409→CONFLICT`, `413→PAYLOAD_TOO_LARGE`, `422→VALIDATION_ERROR`, `429→RATE_LIMITED`, `500→INTERNAL_SERVER_ERROR`, `503→SERVICE_UNAVAILABLE`. Pre-existing custom codes (`SESSION_BUSY`, `RATE_LIMITED`, `PAYLOAD_TOO_LARGE`, `INTERNAL_SERVER_ERROR`, `BAD_REQUEST`) MUST be preserved when set explicitly by the handler.
- AC-QUALITY-ERR-3: `RequestValidationError` (422) responses set `errorCode = "VALIDATION_ERROR"` and put the original Pydantic error list under `details`.
- AC-QUALITY-ERR-4: `traceId` MUST equal the value of the response's `X-Trace-ID` header (which the logging middleware always sets).
- AC-QUALITY-ERR-5: Health endpoints (`/api/health`, `/api/health/ready`) are exempt from the envelope — their existing `{status, reason}` shape is unchanged.

### 18.2 Domain exception hierarchy

A small base hierarchy lives at `app/core/errors.py`:

```python
class AppError(Exception):
    http_status: int = 500
    error_code: str = "INTERNAL_SERVER_ERROR"
    def __init__(self, message: str, *, details: Any | None = None) -> None: ...

class NotFoundError(AppError): http_status = 404; error_code = "NOT_FOUND"
class ConflictError(AppError): http_status = 409; error_code = "CONFLICT"
class ValidationFailedError(AppError): http_status = 422; error_code = "VALIDATION_ERROR"
class SessionBusyError(ConflictError): error_code = "SESSION_BUSY"
```

- AC-QUALITY-EXC-1: Raising any `AppError` subclass from a handler MUST be transformed by the global handler into an `APIError` envelope with the matching `http_status`, `error_code`, and `details`.
- AC-QUALITY-EXC-2: `AppError.__init__` accepts a message and optional `details`. Both are surfaced in the envelope.
- AC-QUALITY-EXC-3: The hierarchy is intentionally small (no per-endpoint subclasses). Endpoints prefer `raise NotFoundError("Quiz session not found.")` over hand-built `JSONResponse`/`HTTPException`.

### 18.3 Modern Python typing

- AC-QUALITY-TYPE-1: All modules under `app/` use PEP 604 / PEP 585 typing — `X | None` (not `Optional[X]`), `list[X]` (not `List[X]`), `dict[K, V]` (not `Dict[K, V]`), `tuple[X, ...]` (not `Tuple[X, ...]`). Enforced by ruff rules `UP006` and `UP007`.
- AC-QUALITY-TYPE-2: Modules opt in to `from __future__ import annotations` so that runtime overhead of new-style hints is zero on Python 3.11.

### 18.4 Orchestration helpers

- AC-QUALITY-ORC-1: `start_quiz` is decomposed into focused private helpers (`_acquire_start_lock`, `_run_initial_synopsis_agent`, `_persist_initial_snapshot`, `_build_start_response`). The public endpoint body becomes a short orchestrator (≤ 60 lines, no `# noqa: C901`).
- AC-QUALITY-ORC-2: Each helper carries a docstring describing inputs, outputs, and side-effects. No new behaviour is added by the refactor — existing tests must continue to pass byte-for-byte.

## 19. Code Quality Hardening — Round 2 (`AC-QUALITY-R2-*`)

**Purpose:** Tighten remaining sharp edges identified by the second-pass audit: overly-broad import guards, duplicated coercion logic, silent diagnostic swallowing, and frontend error-envelope test discipline.

### 19.1 Narrow Import Guards
- AC-QUALITY-R2-IMPORT-1: `try/except` blocks guarding optional module imports MUST catch only `ImportError` (or its subclass `ModuleNotFoundError`). Catching bare `Exception` for an import guard is forbidden because it hides syntax errors, attribute errors, and configuration bugs in the imported module.
- AC-QUALITY-R2-IMPORT-2: When an optional import is missing, the fallback path MUST log at `info` level once at import time (not on every call) so operators can confirm the missing-feature mode at startup.

### 19.2 Canonical Coercion Helper
- AC-QUALITY-R2-COERCE-1: A single utility `app.core.coercion.coerce_to_dict(obj)` exists and is the canonical way to reduce a Pydantic model / dict / `None` / dict-like object into a plain `dict[str, Any]`.
- AC-QUALITY-R2-COERCE-2: The helper accepts: (a) `None` → `{}`, (b) `dict` → shallow copy, (c) any object with `model_dump()` → its dump (Pydantic v1 `dict()` is also accepted as fallback), (d) any other type → raises `TypeError` with the offending type name.
- AC-QUALITY-R2-COERCE-3: Failures inside `model_dump()` are logged at debug level with `error` and `obj_type`, then `{}` is returned (not silently swallowed).

### 19.3 Diagnostic Logging in `_safe_*` Helpers
- AC-QUALITY-R2-LOG-1: Every `_safe_*` helper in `app/agent/llm_helpers.py` that catches `Exception` MUST emit a `logger.debug` event (not `print`, not silent) with the helper name, exception class, and a short context tag.
- AC-QUALITY-R2-LOG-2: The chosen log level is `debug` (not `warning`) because these helpers exist precisely to absorb best-effort lookups; debug-level keeps production logs clean while preserving forensic information when explicitly enabled.

### 19.4 Frontend `ApiError` Envelope Discipline
- AC-QUALITY-R2-FE-ERR-1: A single test utility `frontend/src/test-utils/mockApiError.ts` exposes `mockApiError(code, opts?)` returning a real `ApiError` with `errorCode`, `traceId`, `retriable`, optional `retryAfterMs`, and optional `details`. New error tests SHOULD use this helper instead of bare `new Error("oops")`.
- AC-QUALITY-R2-FE-ERR-2: User-facing components that catch errors from API calls MUST narrow `unknown` to a typed shape and use `errorCode` (when present) to differentiate user messaging — they MUST NOT use `catch (e: any)`.
- AC-QUALITY-R2-FE-ERR-3: An end-to-end Playwright test exercises the full FE↔BE error-envelope round-trip: a mocked BE 429 with `errorCode: "RATE_LIMITED"`, a numeric `Retry-After`, and a body `traceId` MUST result in (a) the FE rendering a rate-limit-specific message, and (b) no console errors / unhandled rejections.

---

## 20. Static Info Content Delivery (`AC-BE-STATIC-CONTENT-*`)

This section defines the backend contract for serving editable markdown-backed
content for frontend informational routes (About, Terms, Privacy, Donate).

### 20.1 Source of Truth
- AC-BE-STATIC-CONTENT-1: Informational page content is configured under `quizzical.frontend.content` in `appconfig.local.yaml`.
- AC-BE-STATIC-CONTENT-2: `GET /api/v1/config` returns `frontend.content` as-is from YAML (no schema mutation of page body text).

### 20.2 Page Keys and Fields
- AC-BE-STATIC-CONTENT-3: The config may contain page keys `aboutPage`, `termsPage`, `privacyPolicyPage`, and `donatePage`.
- AC-BE-STATIC-CONTENT-4: Each page supports `title` (required by FE rendering), `description` (optional), `body` (optional markdown string), and `blocks` (optional legacy structured content array).
- AC-BE-STATIC-CONTENT-5: Backend does not parse or sanitize markdown strings in `body`; markdown rendering is a frontend responsibility.

### 20.3 Runtime Behavior
- AC-BE-STATIC-CONTENT-6: `/config` re-reads YAML per request (unless test override `_YAML` is set), so content edits are reflected without backend code changes.
- AC-BE-STATIC-CONTENT-7: `/config` response continues to include `Cache-Control: public, max-age=<CONFIG_CACHE_SECONDS>`; clients may cache briefly while content remains backend-editable.

---

## 21. Pre-Computed Topic Knowledge Packs (`AC-PRECOMP-*`)

### 21.1 Goals & Scope

**Problem.** The agent currently generates synopsis → character set → baseline questions → images **on demand** for every `/quiz/start`. End-to-end p95 is bounded by the slowest LLM/FAL call. Many topics (e.g. *Star Wars*, *The Office*, *Greek mythology*) are requested repeatedly, so we burn LLM/FAL budget regenerating substantially identical content.

**Goal.** Pre-compute, validate, and persist reusable content bundles ("Topic Knowledge Packs", **TKPs**) so that when a user picks a known topic the user-visible flow becomes a database lookup instead of a generative pipeline. New/unknown topics still flow through the live agent; successful sessions can be **promoted** into TKPs.

**In scope (this section):**
- Synopsis text, synopsis image
- Character / personality set (composition + each character's text + image)
- Baseline questions (text + per-question image where applicable)

**Explicitly out of scope:**
- Per-user adaptive questions (still generated live; they depend on prior answers)
- Final-result profile rendering (still composed live from answers + character set)
- Long-term storage in Azure Blob (not yet provisioned — see §21.10 for the interim and migration plan)

**Non-goals:** TKPs do not encode quiz scoring weights or per-user state. They are pure, reusable *content*.

### 21.2 Glossary

| Term | Definition |
|---|---|
| **Topic** | The canonical thing a quiz is *about*. Has a stable slug + a display name + zero or more aliases. |
| **TKP (Topic Knowledge Pack)** | A versioned, validated bundle binding one topic to one synopsis, one character set, one baseline-question set, and image refs. |
| **Pack version** | Monotonic integer per topic. Only one version per topic is `published` at a time. |
| **Pack status** | `draft → evaluating → validated → published → quarantined → retired`. |
| **Evaluator** | LLM-based judge (with optional web search) that scores correctness/completeness of generated content before persistence. |
| **Promotion** | Moving a `validated` pack from `staging` to `production` (atomic SQL flag flip + cache bust). |
| **Content flag** | User-submitted report that flags a synopsis, character, image, or question as wrong/offensive/low-quality. |

### 21.3 Domain Model (new tables)

All new tables live alongside the existing `characters` / `session_history` / `session_questions` schema. Existing tables are extended additively (no rewrites).

**`topics`** — canonical topic identity.
- `id UUID PK`
- `slug TEXT UNIQUE NOT NULL` — kebab-case, deterministic from `display_name` + disambiguators
- `display_name TEXT NOT NULL`
- `embedding VECTOR(384) NULL` — for fuzzy "did you mean" lookup
- `popularity_rank INT NULL` — derived from telemetry; nullable so cold-start topics still resolve
- `current_pack_id UUID NULL → topic_packs.id` — the live pack served to users
- `flag_count INT NOT NULL DEFAULT 0`
- `created_at`, `last_updated_at`
- `policy_status TEXT NOT NULL DEFAULT 'allowed'` — one of `allowed | restricted | banned` (see §21.9)
- Indexes: `(slug)`, `ivfflat (embedding vector_cosine_ops)`, `(popularity_rank DESC NULLS LAST)`

**`topic_aliases`** — many-to-one alias → topic.
- `(alias_normalized TEXT, topic_id UUID)` PK
- `display_alias TEXT` for UI suggest
- `alias_normalized` is `lower(unaccent(trim(alias)))` to absorb capitalization / accents / whitespace

**`topic_packs`** — versioned bundles.
- `id UUID PK`
- `topic_id UUID NOT NULL → topics.id`
- `version INT NOT NULL` (`UNIQUE(topic_id, version)`)
- `status TEXT NOT NULL` — see §21.2
- `synopsis_id UUID NOT NULL → synopses.id`
- `character_set_id UUID NOT NULL → character_sets.id`
- `baseline_question_set_id UUID NOT NULL → baseline_question_sets.id`
- `evaluator_score SMALLINT NULL` (1–10), `evaluator_report JSONB NULL`
- `model_provenance JSONB NOT NULL` — `{"synopsis":{"model":"gpt-4o-mini","temp":0.4},"characters":{...},...}`
- `cost_cents INT NOT NULL DEFAULT 0`
- `built_at`, `published_at NULL`, `retired_at NULL`
- `built_in_env TEXT NOT NULL` (`local | staging | production`)
- Indexes: `(topic_id, status)`, `(status, published_at DESC)`

**`synopses`** — canonical synopsis bodies (deduped by content hash).
- `id UUID PK`
- `topic_id UUID NOT NULL`
- `content_hash CHAR(64) UNIQUE NOT NULL` — sha256 over normalized JSON body
- `body JSONB NOT NULL` — `{title, summary, perspective?, depth_mode?}`
- `image_asset_id UUID NULL → media_assets.id`
- `evaluator_score SMALLINT NULL`, `evaluator_notes TEXT NULL`
- `flag_count INT NOT NULL DEFAULT 0`
- `created_at`

**`character_sets`** — composition (deduped by ordered character-id hash).
- `id UUID PK`
- `composition_hash CHAR(64) UNIQUE NOT NULL` — sha256 over `[character_id...]` in display order
- `character_ids UUID[] NOT NULL` — ordered (display order is meaningful for the FE)
- `evaluator_score SMALLINT NULL`, `evaluator_notes TEXT NULL`
- `created_at`

**`character_set_topic_map`** — `(character_set_id, topic_id)` PK. A reusable set can serve many topics (e.g. "Heroes Archetype Set" used by both *Greek mythology* and *Marvel*).

**`characters` (extended)** — add additive columns; existing rows are unaffected.
- `canonical_key TEXT` — `lower(unaccent(name))`; **partial unique** index `WHERE canonical_key IS NOT NULL`
- `embedding VECTOR(384) NULL`
- `evaluator_score SMALLINT NULL`, `evaluator_notes TEXT NULL`
- `flag_count INT NOT NULL DEFAULT 0`
- `image_asset_id UUID NULL → media_assets.id` (replaces ad-hoc `image_url`; `image_url` kept for back-compat, populated from asset on read)
- `policy_status TEXT NOT NULL DEFAULT 'allowed'`

**`character_topic_map`** — `(character_id, topic_id)` PK + optional `role TEXT` (e.g. `"hero"`, `"mentor"`).

**`questions`** — canonical question bank.
- `id UUID PK`
- `text_hash CHAR(64) UNIQUE NOT NULL` — sha256 over normalized text
- `text TEXT NOT NULL`
- `options JSONB NOT NULL` — `[{key, text, image_asset_id?}]`; **no scoring weights** (per-quiz scoring is computed live against the active character set)
- `embedding VECTOR(384) NULL`
- `evaluator_score SMALLINT NULL`
- `flag_count INT NOT NULL DEFAULT 0`

**`baseline_question_sets`**
- `id UUID PK`
- `composition_hash CHAR(64) UNIQUE NOT NULL`
- `question_ids UUID[] NOT NULL` — ordered
- `evaluator_score SMALLINT NULL`

**`question_topic_map`** — `(question_id, topic_id)` PK; allows a generic question (e.g. *"How do you spend a free Saturday?"*) to belong to many topics.

**`media_assets`** — image registry (single source of truth; storage URL is just one column).
- `id UUID PK`
- `kind TEXT NOT NULL` — `synopsis | character | question_option`
- `content_hash CHAR(64) UNIQUE NOT NULL` — sha256 of the *bytes*, computed when first cached locally (see §21.10)
- `prompt_hash CHAR(64) NOT NULL` — sha256 over the FAL prompt JSON; allows "we already asked for this image" dedup
- `storage_uri TEXT NOT NULL` — FAL CDN URL today; blob URL after §21.10 migration
- `width INT`, `height INT`, `mime_type TEXT`
- `alt_text TEXT NOT NULL` — required for a11y
- `evaluator_score SMALLINT NULL` (NSFW + relevance score from vision evaluator)
- `license TEXT NOT NULL` — e.g. `"FAL-generated"`, `"CC-BY-4.0"`
- `expires_at TIMESTAMPTZ NULL` — set when storage_uri is a CDN URL with TTL
- `created_at`

**`content_flags`** — user-submitted reports.
- `id UUID PK`
- `target_kind TEXT NOT NULL` — `synopsis | character | question | media | pack`
- `target_id UUID NOT NULL`
- `topic_id UUID NULL`, `session_id UUID NULL`, `client_ip_hash CHAR(64) NOT NULL` (HMAC, see §21.9)
- `reason_code TEXT NOT NULL` — `inaccurate | offensive | low_quality | wrong_topic | other`
- `reason_text TEXT NULL` (≤ 500 chars)
- `status TEXT NOT NULL` — `open | triaging | resolved_fixed | resolved_dismissed`
- `created_at`, `resolved_at NULL`
- Indexes: `(target_kind, target_id)`, `(status, created_at)`

**`precompute_jobs`** — async build ledger.
- `id UUID PK`, `topic_id UUID NULL`, `seed JSONB NOT NULL`
- `status TEXT NOT NULL` — `queued | running | succeeded | failed | rejected`
- `attempt INT NOT NULL DEFAULT 0`, `max_attempts INT NOT NULL DEFAULT 3`
- `tier TEXT NOT NULL` — `cheap | strong | strong+search`
- `evaluator_history JSONB NOT NULL DEFAULT '[]'` — chronological (score, notes, model)
- `cost_cents INT NOT NULL DEFAULT 0`
- `error TEXT NULL`
- `created_at`, `started_at NULL`, `finished_at NULL`

**`evaluator_training_examples`** — labeled examples for evaluator fine-tuning (§21.6).
- `id UUID PK`, `target_kind TEXT`, `target_id UUID`, `evaluator_input JSONB`, `evaluator_output JSONB`, `human_label SMALLINT`, `human_notes TEXT`, `created_at`

### 21.4 Lookup Flow (read path on `/quiz/start`)

```
StartQuizRequest.category
   │
   ▼  Normalise: trim, casefold, unaccent → user_input_norm
   │
   ├── 1. Exact alias match    SELECT topic_id FROM topic_aliases WHERE alias_normalized = $1
   ├── 2. Exact slug match     SELECT id FROM topics WHERE slug = slugify($1)
   └── 3. Vector NN (depth=5)  cosine(embedding, embed(user_input_norm)) ≥ τ_match
            │
            ▼ if best NN < τ_match  →  cache MISS  →  live agent flow (existing path)
            │
            ▼ if hit              →  TKP HIT
                 ▼
                 SELECT * FROM topic_packs WHERE topic_id = $1 AND status = 'published'
                 │
                 ├─ joined synopsis, character_set + characters[], baseline_question_set + questions[], media_assets
                 ├─ build initial GraphState in-memory (skip bootstrap/generate_characters/generate_baseline_questions nodes)
                 ├─ persist a session_history row referencing the pack (new column session_history.served_pack_id)
                 └─ return FrontendStartQuizResponse synchronously (no background "warm" task needed for these three artefacts)
```

- **Threshold τ_match** is configurable (`AC-PRECOMP-LOOKUP-3`); default `0.86` cosine, tuned on the eval harness.
- Cache HIT also writes a small `topic_hits` counter (Redis HINCRBY with TTL bucket per day) feeding `topics.popularity_rank`.
- A cache HIT MUST still go through `verify_turnstile` and rate limiting — the lookup path is faster but not less abusable.

### 21.5 Build / Validate / Persist Pipeline (write path)

The pipeline is an async worker that pulls from `precompute_jobs`. Triggered by:
1. **Operator seed** — CSV/YAML of priority topics enqueued by a CLI (`scripts/enqueue_seed_topics.py`).
2. **Telemetry-driven** — nightly job enqueues the top-N "miss" topics from the last 7 days.
3. **Successful live session** — when `/quiz/status` returns `finished` for a topic with no published pack, the post-completion hook enqueues a `seed = {from_session: <id>}` job.

```
┌──────────────────────────────────────────────────────────────┐
│ enqueue → precompute_jobs.status=queued                       │
└──────────────────────────────────────────────────────────────┘
                  │
                  ▼
┌──────────────────────────────────────────────────────────────┐
│ Worker picks job                                              │
│  - Acquire Redis lock rl:precomp:{topic_id} (TTL 30m)         │
│  - tier := 'cheap'                                            │
│  - For attempt in 1..max_attempts:                            │
│      generate(tier) → artefacts                               │
│      score := evaluator(artefacts, tier_eval=tier)            │
│      record evaluator_history                                 │
│      if score ≥ τ_pass: break                                 │
│      if attempt == 1: tier := 'strong'                        │
│      if attempt == 2: tier := 'strong+search'                 │
│  if score < τ_pass after max_attempts:                        │
│      status := 'rejected', enqueue manual review              │
│      EXIT (do not persist artefacts)                          │
│  else:                                                        │
│      transactional persist:                                   │
│        - upsert characters by canonical_key (dedup)           │
│        - upsert questions by text_hash (dedup)                │
│        - upsert character_set / baseline_question_set         │
│        - insert synopses, media_assets                        │
│        - insert topic_packs row (status='validated', new ver) │
│      enqueue image generation jobs (separate worker)          │
└──────────────────────────────────────────────────────────────┘
                  │
                  ▼ (separate stage)
┌──────────────────────────────────────────────────────────────┐
│ Image worker                                                  │
│  - For each missing media_asset (synopsis, characters,        │
│    optionally per-question):                                  │
│      generate via FAL                                         │
│      vision-evaluator scores relevance + safety               │
│      if pass: insert media_assets, link to artefact           │
│      else: regenerate (max 2 retries) or mark pack            │
│            'validated_text_only' (publishable without imgs)   │
└──────────────────────────────────────────────────────────────┘
                  │
                  ▼
┌──────────────────────────────────────────────────────────────┐
│ Promotion (manual or automatic per env policy)                │
│  publish(pack_id) :=                                          │
│    BEGIN;                                                     │
│      UPDATE topic_packs SET status='retired', retired_at=now()│
│        WHERE topic_id=$t AND status='published';              │
│      UPDATE topic_packs SET status='published',               │
│        published_at=now() WHERE id=$pack_id;                  │
│      UPDATE topics SET current_pack_id=$pack_id WHERE id=$t;  │
│      INVALIDATE Redis cache key tk:pack:$t;                   │
│    COMMIT;                                                    │
└──────────────────────────────────────────────────────────────┘
```

- Worker concurrency, per-tier model choice, daily $-cap, and τ thresholds all live under `appconfig.local.yaml → quizzical.precompute`.
- All worker LLM/FAL calls reuse the existing retry/timeout/semaphore machinery (§16, §17.1) — no new resilience code paths.

### 21.6 Evaluation Model

The evaluator is the gate; nothing persists without its approval.

**Tiered ladder** (cost-aware, per artefact):
1. **Tier-1 cheap** — small/fast model (e.g. `gpt-4o-mini`, configurable). Produces score 1–10 + structured `notes`.
2. **Tier-2 strong** — larger model (`gpt-4o` / `claude-3.5-sonnet`). Triggered when Tier-1 < `τ_strong_trigger` *or* on Tier-1 self-uncertainty signal.
3. **Tier-3 strong + web search** — adds a search tool call (Perplexity / OpenAI w/ browsing). Triggered for **factual** artefacts (synopsis text and questions whose `requires_factual_check=true`). Mandatory for any synopsis where `topic.policy_status='restricted'`.

**What evaluator scores per artefact:**

| Artefact | Rubric (each 1–10) |
|---|---|
| Synopsis | factual_accuracy, coverage, tone, length_fit, no_spoilers |
| Character set | distinctness, coverage_of_archetypes, balance, on_topic |
| Single character | on_topic, internally_consistent, distinctive_voice, length_fit |
| Question | clarity, on_topic, options_balanced, no_leading_phrasing, no_pii_or_unsafe |
| Media | relevance_to_subject, safety (no NSFW, no minors-in-suggestive-context, no logos/marks), aesthetic |

`evaluator_score` for the pack is the **min** of constituent scores (one bad character poisons the pack).

**Pass thresholds** (defaults, all configurable):
- `τ_pass = 8` (per-artefact min)
- `τ_strong_trigger = 7`
- `τ_match = 0.86` (lookup cosine)

**Fine-tuning loop (§21.6.1):**
- Every operator action in the review queue (approve / reject / edit) writes to `evaluator_training_examples`.
- A weekly export script produces a JSONL fine-tune dataset.
- A/B harness in `Analysis/run_experiment.py` (existing infra) compares evaluator versions on a held-out set; we only promote a fine-tuned evaluator when it strictly improves precision **and** recall on the held-out set vs the current one.

### 21.7 Promotion / Deployment

**Two logical environments share the production DB**, separated by `topic_packs.built_in_env` and `topic_packs.status`:
- `staging` packs are built nightly from new dev seeds; FE never reads them.
- `production` packs are reads-only for the hot path; promotion is the SQL flip in §21.5.

**Migration of pre-built packs from dev → prod:**
1. Dev runs the pipeline against the dev DB; produces packs with `built_in_env='local'`.
2. `scripts/export_packs.py` dumps a content-addressed JSON archive (synopses, characters, questions, sets, packs, media manifest with content hashes only — *not* the image bytes).
3. Image bytes are referenced via stable FAL URLs (today) or blob URLs (after §21.10); the image worker rehydrates if needed.
4. `scripts/import_packs.py` runs in production, idempotent on `content_hash` / `composition_hash` — re-imports are no-ops.
5. Promotion is a **manual** step (`scripts/promote_pack.py --topic-slug <slug> --version <n>`) that requires `OPERATOR_TOKEN`. No automatic prod publish from CI.

**Rollback:** `scripts/promote_pack.py --rollback <topic-slug>` repoints `topics.current_pack_id` to the prior `published` (now `retired`) version and flips statuses back. The retired version is preserved indefinitely.

### 21.8 User Flagging & Self-Healing

**API surface:**
- `POST /api/v1/content/flag` — body `{target_kind, target_id, topic_id?, session_id?, reason_code, reason_text?}`. Authenticated by Turnstile + rate-limited per-IP (stricter than the default bucket: 5/min, 30/day).
- `GET /api/v1/content/flag/policy` — read-only constants (allowed reason codes, max length).

**Operator surface (admin auth, **not** user-facing):**
- `GET /api/v1/admin/flags?status=open&kind=synopsis`
- `POST /api/v1/admin/flags/{id}/resolve` body `{action: "fix"|"dismiss", new_pack_id?: UUID}`
- `POST /api/v1/admin/packs/{pack_id}/quarantine`

**Auto-quarantine:**
- When `flag_count` for a `target` crosses `Q_threshold` (default 3 distinct IP hashes / 24h), the **enclosing pack** is auto-moved to `quarantined`.
- Quarantined packs are skipped by the lookup → live agent path is used → next /quiz/start regenerates content.
- An operator triages from the queue; a fix can be: (a) edit + re-evaluate, (b) regenerate, (c) dismiss.

**Why per-IP-hash, not raw IP:** flag counts use `HMAC-SHA256(client_ip, FLAG_HMAC_SECRET)` so the DB never stores raw IPs (GDPR alignment + reduces blast radius of a DB exposure).

### 21.9 Content Safety & Policy

- **Topic policy gate.** Before any pack is built for a topic, the topic itself is classified by a policy LLM (one shot, cached) → `allowed | restricted | banned`. `banned` topics never enter the queue. `restricted` topics force Tier-3 evaluator (web search) and a stricter `τ_pass=9`.
- **Real-person guardrails.** Synopses and characters about identifiable real living persons are restricted by default: the evaluator must verify (a) public-figure status, (b) factual claims sourced. No defamation / sexual content about real people.
- **Image safety.** The vision evaluator must verify: no minors in suggestive context, no recognisable trademarks/logos, no NSFW unless explicitly allowed by topic. FAL's safety filter is a *first* gate, our evaluator is the *second*.
- **Prompt injection in user category strings.** The category text is treated as data, never as part of any system prompt without escaping. Length cap: 200 chars. Disallowed control chars stripped. (Existing `app.security.input_validation` is reused.)
- **PII in flags.** `reason_text` is scrubbed by the existing `_redact_in_str` (JWT/PAN/email) before write.
- **Logging.** All operator actions on the review queue produce immutable audit rows in a new `audit_log` table (target, actor_id, action, before_hash, after_hash, ts).

### 21.10 Image Storage (interim plan, no Azure Blob yet)

**Phase 0 — current (FAL CDN URLs).** `media_assets.storage_uri = FAL CDN URL`. Set `expires_at` from FAL response headers (or a conservative 30-day default). This works *today* and changes nothing in the FE.

**Phase 0.5 — local rehosting fallback.** A small worker script `scripts/rehost_expiring_media.py` runs daily; for any `media_assets` row with `expires_at < now() + 7 days`, re-download bytes, store **temporarily** in a local Postgres `BYTEA` column on `media_assets` (`bytes_blob BYTEA NULL`), and rewrite `storage_uri` to a backend-served route `GET /api/v1/media/{asset_id}`. The endpoint streams the bytes with `Cache-Control: public, max-age=31536000, immutable` and a content-addressed ETag. Postgres BYTEA is **only** the bridge; `bytes_blob` is dropped after Phase 1.

**Phase 1 — Azure Blob (when provisioned).**
- New env var `BLOB_BASE_URL`; new `media_assets.storage_provider TEXT` column.
- Migration worker copies bytes from `bytes_blob` (or re-downloads from FAL CDN) into blob, rewrites `storage_uri`, drops `bytes_blob`. The single-column abstraction means no FE / service-layer changes.

**Why:** the design never makes the FE "know" where images live. Everything is `media_assets.storage_uri`. We can swap providers without touching the agent or the FE.

### 21.11 Configuration

New section in `appconfig.local.yaml`:

```yaml
quizzical:
  precompute:
    enabled: true
    workers:
      build_concurrency: 2
      image_concurrency: 4
    daily_budget_usd: 25.00
    pack_ttl_days: 365
    thresholds:
      tau_pass: 8           # min per-artefact evaluator score to persist
      tau_strong_trigger: 7 # below this → escalate evaluator tier
      tau_match: 0.86       # cosine similarity for lookup hit
      max_build_attempts: 3
      flag_quarantine_count: 3
      flag_quarantine_window_hours: 24
    models:
      tier_cheap:    { provider: openai, model: gpt-4o-mini,        max_tokens: 1200 }
      tier_strong:   { provider: openai, model: gpt-4o,             max_tokens: 1800 }
      tier_search:   { provider: perplexity, model: sonar-pro,      max_tokens: 1800 }
      evaluator:     { provider: openai, model: gpt-4o-mini,        max_tokens: 800 }
      evaluator_strong: { provider: openai, model: gpt-4o,          max_tokens: 800 }
      vision_eval:   { provider: openai, model: gpt-4o-mini,        max_tokens: 400 }
    images:
      synopsis: true
      characters: true
      question_options: false   # cost gate; opt-in per topic later
    seed_sources:
      - path: configs/precompute/seed_topics.csv
        priority: 1
    promotion:
      auto_publish_in: [staging]   # never auto-publish to production
```

### 21.12 API Surface (new endpoints)

| Method | Path | Auth | Rate limit | Purpose |
|---|---|---|---|---|
| `POST` | `/api/v1/content/flag` | Turnstile | 5/min, 30/day per IP | User reports bad content |
| `GET` | `/api/v1/content/flag/policy` | none | default | Returns valid reason codes & limits |
| `GET` | `/api/v1/topics/suggest?q=...` | none | default | Type-ahead from `topic_aliases` (LIKE) + `topics` (NN) |
| `POST` | `/api/v1/admin/flags/{id}/resolve` | `OPERATOR_TOKEN` | strict | Operator triage |
| `POST` | `/api/v1/admin/packs/{pack_id}/promote` | `OPERATOR_TOKEN` | strict | Manual promote/rollback |
| `GET` | `/api/v1/admin/precompute/jobs` | `OPERATOR_TOKEN` | strict | Worker observability |
| `GET` | `/api/v1/media/{asset_id}` | none | default | Phase-0.5 image streaming (if storage_uri is local) |

The user-visible `/quiz/start` contract does **not** change; only its internal implementation does.

### 21.13 Acceptance Criteria

**Lookup correctness**
- `AC-PRECOMP-LOOKUP-1`: Exact alias match returns the topic's `current_pack_id` in ≤ 25 ms p95 (DB lookup only, excluding network).
- `AC-PRECOMP-LOOKUP-2`: Vector NN match below `τ_match` does NOT return a pack — falls through to live agent.
- `AC-PRECOMP-LOOKUP-3`: `τ_match`, `τ_pass`, `τ_strong_trigger` are read from `appconfig.precompute.thresholds`; missing keys fall back to documented defaults.
- `AC-PRECOMP-LOOKUP-4`: A `quarantined` or `retired` pack is NEVER returned even if `topics.current_pack_id` still points at it (defensive double-check in the SELECT).
- `AC-PRECOMP-LOOKUP-5`: A pack HIT must still pass Turnstile + rate limiting; any failure preserves existing 401/403/429 semantics.

**Build pipeline**
- `AC-PRECOMP-BUILD-1`: `precompute_jobs.status` transitions only along `queued → running → (succeeded|failed|rejected)`.
- `AC-PRECOMP-BUILD-2`: A job whose evaluator never reaches `τ_pass` after `max_build_attempts` is marked `rejected` and produces ZERO rows in `topic_packs`, `synopses`, `characters` (other than chars already deduped from prior packs), `questions`.
- `AC-PRECOMP-BUILD-3`: Tier escalation order is exactly `cheap → strong → strong+search`. The job ledger records the model used at each attempt.
- `AC-PRECOMP-BUILD-4`: Persistence is one DB transaction; partial failure rolls back atomically (no orphan synopses or character sets).
- `AC-PRECOMP-BUILD-5`: Daily spend (sum of `precompute_jobs.cost_cents` for the calendar day in UTC) ≤ `daily_budget_usd × 100`. The 11th hour cost check before each new attempt skips and re-queues with `delayed_until=tomorrow`.

**Dedup invariants**
- `AC-PRECOMP-DEDUP-1`: Inserting a character whose `canonical_key` already exists is a no-op (update embedding/score if missing); the existing UUID is reused.
- `AC-PRECOMP-DEDUP-2`: Inserting a synopsis / question / character_set / baseline_set whose `*_hash` already exists is a no-op.
- `AC-PRECOMP-DEDUP-3`: A media asset whose `prompt_hash` already exists with `evaluator_score ≥ τ_pass` is reused — no FAL call is made.

**Promotion**
- `AC-PRECOMP-PROMOTE-1`: `publish(pack_id)` is atomic — under concurrent calls, exactly one pack is `published` per topic.
- `AC-PRECOMP-PROMOTE-2`: A successful publish invalidates Redis key `tk:pack:{topic_id}` before COMMIT releases.
- `AC-PRECOMP-PROMOTE-3`: Production promotion REQUIRES `OPERATOR_TOKEN`; CI environments have it unset.
- `AC-PRECOMP-PROMOTE-4`: Rollback restores both `topics.current_pack_id` and the prior pack's `status='published'` in one transaction.

**Flagging & quarantine**
- `AC-PRECOMP-FLAG-1`: `POST /content/flag` validates `reason_code` against an allowlist; unknown values → 422.
- `AC-PRECOMP-FLAG-2`: `reason_text` is length-capped (≤ 500) and PII-scrubbed before insert.
- `AC-PRECOMP-FLAG-3`: `client_ip_hash = HMAC-SHA256(ip, FLAG_HMAC_SECRET)`; raw IP never persists.
- `AC-PRECOMP-FLAG-4`: When distinct `client_ip_hash` count for a (target_kind, target_id) ≥ `flag_quarantine_count` within `flag_quarantine_window_hours`, the enclosing pack is moved to `quarantined` in one transaction with the flag insert.
- `AC-PRECOMP-FLAG-5`: A quarantined pack causes the next `/quiz/start` for that topic to take the live-agent path; FE behaviour is identical to a cache miss.
- `AC-PRECOMP-FLAG-6`: Operator resolve actions write an `audit_log` row with `actor_id`, action, before/after hashes.

**Safety**
- `AC-PRECOMP-SAFETY-1`: A topic with `policy_status='banned'` cannot be enqueued; the worker returns 4xx-equivalent ledger error.
- `AC-PRECOMP-SAFETY-2`: A `restricted` topic forces evaluator tier `strong+search` and `τ_pass=9`.
- `AC-PRECOMP-SAFETY-3`: User-supplied category text is never substituted into a system prompt; only into a delimited `<user_input>` block. (Reuses existing input validation; new test asserts no leakage of injection markers like `</system>`.)
- `AC-PRECOMP-SAFETY-4`: All vision-evaluator rejections are recorded with reason code (NSFW, minor, logo, off-topic) for offline review.

**Storage / images**
- `AC-PRECOMP-IMG-1`: A media asset's `storage_uri` may be a FAL URL, a backend `/media/{id}` route (Phase 0.5), or a blob URL (Phase 1) — the FE renders identically.
- `AC-PRECOMP-IMG-2`: `expires_at` ≤ 7 days triggers rehost on the next worker run.
- `AC-PRECOMP-IMG-3`: The rehost endpoint sets `Cache-Control: public, max-age=31536000, immutable` and a strong ETag derived from `content_hash`.

**Observability**
- `AC-PRECOMP-OBS-1`: Each pack lookup emits a structured log `precompute.lookup` with `{topic_slug, hit, source: alias|slug|nn, latency_ms}`.
- `AC-PRECOMP-OBS-2`: Each build attempt emits `precompute.build` with `{job_id, attempt, tier, score, cost_cents}`.
- `AC-PRECOMP-OBS-3`: Daily spend gauge is exposed for scrape (`precompute_daily_spend_usd`).

### 21.14 Test Plan (high-level)

| Layer | Tests |
|---|---|
| Unit | Slug/alias normaliser; tier escalation policy; `min()` aggregation of evaluator scores; HMAC determinism; quarantine threshold counter; promote/rollback transaction sketch with sqlite mock. |
| Integration | `/content/flag` happy path + 422; `/admin/packs/.../promote` requires token; `/quiz/start` cache HIT bypasses agent (assert no LLM call mocked); cache MISS still reaches agent. |
| Reliability | Worker survives Redis outage (job re-claimed after lock TTL); image worker idempotent on retry. |
| Security | `restricted` topic forces tier 3; banned topic refused; injection probe `</system>` in category never appears in evaluator input; raw IP never written. |
| Performance | Cache HIT p95 ≤ 100 ms (DB+JSON build, no LLM); promote/rollback ≤ 50 ms p95. |
| Smoke | Seed → build → publish → /quiz/start hits → /quiz/next still works (adaptive path unchanged). |

### 21.15 Frontend Touchpoints (new — see also frontend spec)

- Type-ahead for known topics powered by `/api/v1/topics/suggest` (with debounce). FE never depends on a pack existing — it just gets a smoother UX when one does.
- "Report this content" button on synopsis and per-character cards; modal collects `reason_code` + optional text; calls `POST /content/flag`; success shows a quiet toast.
- No FE awareness of `pack_id` — packs are an internal optimisation. The shape of `/quiz/start` and `/quiz/status` responses is unchanged.

### 21.16 Migration Plan (rollout sequence)

1. **Schema migration** (forward-only). New tables + extended columns; backfill `characters.canonical_key` from existing `name`. Existing data unaffected.
2. **Read-path shim** (off by default). Lookup logic added; feature-flag `precompute.enabled=false` keeps current behaviour.
3. **Build worker + admin endpoints** ship behind operator token.
4. **Seed and dry-run** in dev (~50 priority topics).
5. **Enable lookup in staging**, monitor for regressions.
6. **Promote a small published set to production** (manual), keep `precompute.enabled=true` for that subset only.
7. **Expand seed list**; auto-enqueue from telemetry.

Each step is independently revertible.

---

### 21.17 Review Log

The plan in §21.1–§21.16 is the integrated final design. It evolved through the six successive reviews summarised below; each note documents what changed and why.

**Review 1 — Senior-engineer critical review.**
- *Concern:* Original draft tied a TKP 1-to-1 to a topic, which would explode storage and prevent reuse. *Change:* introduced `character_sets` and `baseline_question_sets` as first-class entities with `composition_hash`, plus M:N maps. Sets and characters are now reusable across topics.
- *Concern:* Original draft had no concept of "this content is for staging" vs "live in prod". *Change:* added `topic_packs.built_in_env` + the explicit promote/rollback flow (§21.7) so a bad nightly build never poisons production.
- *Concern:* "Iterate until evaluator agrees" with no cap is a runaway cost vector. *Change:* `max_build_attempts=3`, daily $-cap, hard `rejected` terminal state with manual review.
- *Concern:* The plan promoted automatically from staging. *Change:* production promotion is **manual** behind `OPERATOR_TOKEN`; `auto_publish_in: [staging]` only.
- *Concern:* No rollback path. *Change:* `promote_pack.py --rollback` + retired packs preserved indefinitely.
- *Concern:* Admin endpoints originally bare. *Change:* operator token required; rate limit stricter; audit-log table for every operator action.

**Review 2 — Performance review.**
- Added `topic_aliases.alias_normalized` indexed exact-match path *before* embedding NN — most lookups are exact (autocomplete picks the suggestion).
- Added vector NN as a final fallback only, with `ivfflat`/`vector_cosine_ops` index (matches existing `synopsis_embedding` pattern).
- Specified that pack HIT joins are pre-warmed via a single denormalised SELECT (synopsis + character_ids array → `unnest` join). Defined p95 budget (`AC-PRECOMP-LOOKUP-1`: 25 ms DB).
- `tk:pack:{topic_id}` Redis cache key (TTL e.g. 1 h) absorbs the JOIN cost; invalidated transactionally on promote.
- Build pipeline split into **two** workers (text + image) so an FAL outage doesn't stall text validation.
- `Cache-Control: public, max-age=31536000, immutable` on the rehost media route — once an image hash is fixed, browsers cache forever.
- Pack lookup writes to a Redis `HINCRBY` daily-bucket counter rather than a Postgres update on every read (write contention).

**Review 3 — Quality review (content correctness).**
- Per-artefact rubrics added (§21.6 table) so "score 8" is unambiguous.
- Pack score = **min** of constituents — one bad character poisons the pack and forces re-eval.
- Tier-3 evaluator MUST use web search for factual artefacts and for `restricted` topics; `τ_pass` raised to 9 there.
- Vision evaluator added as a second safety gate after FAL's own filter.
- Fine-tune loop (§21.6.1): every operator decision in the review queue becomes a labeled training example; A/B harness reuses existing `Analysis/run_experiment.py` infra; only deployed if it strictly improves precision and recall on the held-out set.
- Real-person guardrail explicit (§21.9).

**Review 4 — Security & safety review.**
- Topic policy gate added (`policy_status: allowed|restricted|banned`).
- User-supplied category never enters a system prompt — only a delimited `<user_input>` block (`AC-PRECOMP-SAFETY-3`).
- `client_ip_hash = HMAC-SHA256(ip, FLAG_HMAC_SECRET)` — raw IPs never written to flags table.
- Operator endpoints behind `OPERATOR_TOKEN`; CI never has it.
- Audit log table for every operator action (`audit_log`).
- Rate limits on `/content/flag` are **stricter** than default (5/min, 30/day per IP) to blunt brigading.
- Image safety: vision evaluator rejection reason codes captured for offline review.
- Quarantine is **automatic** beyond a small distinct-IP threshold so a wave of valid flags depopulates a bad pack without operator latency.
- All operator actions (and the worker pipeline) reuse the existing `verify_turnstile` / Turnstile-bypass / rate-limit infrastructure — no new auth code paths.

**Review 5 — Cost optimisation review.**
- Tiered model ladder (cheap → strong → strong+search) with explicit escalation triggers (§21.6).
- Hard daily $-cap with pre-attempt check (`AC-PRECOMP-BUILD-5`).
- Aggressive dedup on **prompt_hash** for images — same prompt = same FAL call deduped (`AC-PRECOMP-DEDUP-3`).
- Aggressive dedup on **canonical_key** for characters and **text_hash** for questions; cross-topic reuse is the normal case.
- Image generation gated behind text-pack validation pass — never generate images for content we'll throw away.
- `question_options` images **off by default** (per-question pictures explode FAL spend); per-topic opt-in.
- Topic priority tiered from telemetry (popularity_rank); we only pre-build what users actually ask for.
- A pack costs roughly `1 synopsis + 5 characters + 8 baseline questions + ~6 images = ~$0.20–$0.50` at cheap tier. With ~100 packs/day that's ~$25 — matches default budget.
- Evaluator runs at the cheap tier by default; only escalated when a generation failed validation, not on every artefact.
- Pack TTL of 365 days + monitoring on `flag_count` rate detects drift without burning continuous re-evaluation cost.

**Review 6 — Holistic product / UX review.**
- The pack mechanism is **invisible to the user** — `/quiz/start` and `/quiz/status` shapes are unchanged. A pack hit just feels like a fast quiz; a miss feels like the current quiz. There's no "this topic is precomputed" badge.
- Type-ahead `/topics/suggest` makes the "fun, smart, useful" promise tangible — users see the breadth of the topic catalog as they type, which encourages exploration.
- "Report" button is small, friendly, and rate-limited so it doesn't become a brigade vector but still gives users agency.
- The live-agent path for unknown topics is the same path as today — there is no UX regression for the long-tail.
- Adaptive questions and final results are still **always** generated live, so the experience stays personal even on a cached topic — only the static scaffolding is reused.
- Promotion is operator-controlled, so a "hot" cultural moment (say, a Marvel release) can be hand-curated and pushed without waiting for the nightly worker.
- Storage abstraction (`media_assets.storage_uri`) means swapping from FAL CDN → blob is invisible to users.
- The same flag → quarantine → live-regenerate loop turns negative user feedback into automatic content improvement, reinforcing the "smart" feel.
- Critically: the agent itself is unchanged. If the precompute system is ever turned off (`precompute.enabled=false`), the product still works exactly as it does today. The whole §21 is purely additive, optional, revertible.

---

### 21.18 Iteration 2 — Six Additional Reviews

The first review log (§21.17) hardened the *structure* of the precompute system. This second pass tightened it for **cold-start UX, end-to-end latency, evaluator quality control, abuse vectors, marginal cost, and Azure migration**. Material changes are listed per-review and folded into the canonical sections below.

#### Review 7 — Objectives revisited (does it actually feel instant?)

*What was missing.* §21 made `/quiz/start` faster on a HIT but said nothing about (a) **what a MISS feels like next to a HIT**, (b) the cold-start case where almost no packs exist yet, (c) how the FE knows to optimise either path.

*Decisions:*
1. **Two-phase response on `/quiz/start`** (always, for both HIT and MISS):
   - Phase A (≤ 200 ms p95): a synchronous `FrontendStartQuizResponse` with `quiz_id`, `pack_status: "hit"|"miss"|"degraded"`, and **either** the full synopsis (HIT) or a `placeholder_synopsis: { title: <category>, summary: null, image_url: null }` (MISS).
   - Phase B: existing polling on `/quiz/status` populates the synopsis/characters as the live agent produces them. The FE never blocks on Phase B for the HIT path.
   - This makes "first paint" identical for HIT and MISS — only the subsequent fields hydrate later for MISS. (`AC-PRECOMP-OBJ-1`)
2. **Cold-start seeding.** Ship a small bundled "starter catalog" (~50 evergreen topics: *Star Wars*, *Greek mythology*, *The Office*, *cats vs dogs*, *Hogwarts houses*, …) as **versioned JSON** in `configs/precompute/starter_packs/*.json`, hashed and imported by `scripts/import_packs.py` on first deploy. Guarantees a non-empty hit rate from day one. (`AC-PRECOMP-OBJ-2`)
3. **Pack hit telemetry surfaced at `/healthz/precompute`** (operator route): `{packs_published, hit_rate_24h, miss_rate_24h, top_misses_24h: [...]}`. Drives the next seed batch without a separate analytics tool. (`AC-PRECOMP-OBJ-3`)
4. **"Recently used" personal cache** — a per-IP-hash key in Redis (`tk:recent:{ip_hash}`) holds the last 5 topics, surfaced on the FE landing page. Doesn't bypass any auth/rate-limit; just primes UX. (`AC-PRECOMP-OBJ-4`)

#### Review 8 — Performance, end-to-end (not just DB)

*What was missing.* §21 specified the lookup p95 (25 ms) but not the full request budget, network egress, or the long-tail behaviour when many packs share a hot character.

*Decisions:*
1. **Materialised view `mv_topic_pack_resolved`** keyed by `topic_id`, joining the `published` pack with its synopsis, character set, baseline question set, and asset URIs. Refreshed `CONCURRENTLY` (so reads never block) inside `publish(pack_id)`. The hot-path SELECT becomes one row + one array-unnest. (`AC-PRECOMP-PERF-1`)
2. **Redis pack cache:** `tk:pack:{topic_id}` stores the JSON-serialised resolved pack (~5–20 KB). TTL 1 h, with a SETNX-style "fill lock" to prevent thundering herd on cache miss. Invalidated transactionally by promote/quarantine. (`AC-PRECOMP-PERF-2`)
3. **HTTP-level performance:**
   - HTTP/2 keep-alive on uvicorn (already on); enable `Server-Push` not used (deprecated), but **HTTP `103 Early Hints`** on `/quiz/start` for resource preload of expected media URIs on HIT. Costs nothing if the proxy strips it. (`AC-PRECOMP-PERF-3`)
   - `Cache-Control: private, max-age=300, stale-while-revalidate=3600` on `/quiz/status` once `state == finished`, so back-button to a finished quiz is instant.
4. **Image edge delivery (storage-uri-agnostic):**
   - Phase 0 (today): proxy FAL CDN URLs straight to the FE. Add `<link rel="preload" as="image">` headers via Early Hints.
   - Phase 0.5 (rehost): the backend `GET /api/v1/media/{asset_id}` endpoint MUST set `Cache-Control: public, max-age=31536000, immutable` and an `ETag` derived from `media_assets.content_hash`. Path is content-addressed (`/media/{asset_id}` resolves stably for the lifetime of the asset). (`AC-PRECOMP-PERF-4`)
   - Phase 1 (Azure Blob + Front Door): `storage_uri` becomes `https://media.quizzicalai.com/{content_hash[0:2]}/{content_hash}.{ext}`; CDN handles all caching. The FE *still* doesn't change — the URL is opaque.
5. **Pre-warmed evaluator embeddings:** topic embeddings are computed once and cached at write time; the lookup-NN path uses `pgvector` only — no model call on the read path. (`AC-PRECOMP-PERF-5`)
6. **"Tail-amplification" guard:** if a single character belongs to > 200 packs, its `image_asset_id` is pinned in Redis (`media:hot:{asset_id}`) so an icon-rebuild storm can't blow Postgres I/O. (`AC-PRECOMP-PERF-6`)
7. **End-to-end p95 budget on `/quiz/start` (HIT):** 250 ms server time (auth + RL + lookup + JSON serialise). End-to-end (DC-to-FE) target 350 ms p95. Recorded via the existing `Server-Timing` machinery (§17.4).

#### Review 9 — Quality of responses and evaluation techniques

*What was missing.* §21 named scoring rubrics but did not specify (a) reproducibility, (b) drift detection, (c) what "the evaluator agrees" means when a single judge can hallucinate, or (d) how prompts themselves are versioned.

*Decisions:*
1. **Versioned prompts for both generators and evaluators.** Every prompt template gets a semantic-version string and a hash; both are stored in `topic_packs.model_provenance` and in `precompute_jobs.evaluator_history`. Re-running the same job at a later prompt version is a *new* attempt, not a no-op. (`AC-PRECOMP-QUAL-1`)
2. **Two-judge consensus on factual artefacts.** For synopses and any question with `requires_factual_check=true`, two evaluator runs with different seeds (or different providers when configured) MUST both return `score ≥ τ_pass`; min is taken. A divergence > 2 points triggers Tier-3 (web search) automatically. (`AC-PRECOMP-QUAL-2`)
3. **Golden held-out set.** A curated dataset (~200 hand-graded items per artefact kind) lives at `Analysis/golden/precompute_*.jsonl`. Every change to evaluator prompts or model selection MUST be A/B'd against it; promotion gated on **strict improvement** in precision *and* recall vs the current evaluator. The harness reuses `Analysis/run_experiment.py`. (`AC-PRECOMP-QUAL-3`)
4. **Evaluator self-consistency probe.** Once per week, replay 5% of randomly-sampled previously-validated artefacts through the current evaluator. If pass-rate drops > 10 percentage points, alert and pause new persistence (`status='evaluator_drift_paused'`). (`AC-PRECOMP-QUAL-4`)
5. **Calibration via reason codes.** Evaluator output is structured: `{score, blocking_reasons:[...], non_blocking_notes:[...]}`. A `blocking_reason` (e.g. `"factual_error"`, `"unsafe"`) forces rejection regardless of score. Notes feed the operator review queue but don't block. (`AC-PRECOMP-QUAL-5`)
6. **Source-citation requirement for Tier-3.** When the evaluator uses web search, it MUST attach `sources: [{url, snippet}]` to every `blocking_reasons` entry. Sources are persisted on `topic_packs.evaluator_report` for auditability. (`AC-PRECOMP-QUAL-6`)
7. **Cross-pack consistency.** When a character with the same `canonical_key` is judged with materially different `profile_text` across two packs (cosine on embeddings < 0.85), the build is rejected and the existing canonical character is reused. (`AC-PRECOMP-QUAL-7`)

#### Review 10 — Security & safety, second pass

*What was missing.* §21 covered topic-policy and IP hashing but glossed over outbound SSRF, prompt-injection in retrieved web content, abuse vectors on the new public endpoints, and supply-chain integrity of imported packs.

*Decisions:*
1. **Outbound SSRF posture.** All FAL/CDN/web-search calls go through a single `httpx` client with a deny-list (`localhost`, `127.0.0.0/8`, `169.254.0.0/16`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `::1`, link-local). DNS rebind is mitigated by resolving once and pinning the IP for the request. (`AC-PRECOMP-SEC-1`)
2. **Web-search content is *data*.** Snippets returned to the evaluator are wrapped in `<retrieved>...</retrieved>` blocks; system prompt explicitly instructs the model to *never* execute instructions from inside those blocks. Tested with a regression probe (`"</retrieved><system>..."` injection). (`AC-PRECOMP-SEC-2`)
3. **`/topics/suggest` abuse.** Returns at most 8 results, keys queries shorter than 2 chars are rejected, rate-limited 60/min per IP. No exact-substring search of `display_name` (which would let attackers enumerate the topic catalog at low cost — they get prefix + alias matches only). (`AC-PRECOMP-SEC-3`)
4. **Operator endpoints behind step-up auth.** `OPERATOR_TOKEN` is a long-lived bearer; promote/rollback additionally requires a TOTP-style second factor (`X-Operator-2FA`) for production environment. CI is allowed promote-to-staging only. (`AC-PRECOMP-SEC-4`)
5. **Pack import integrity.** `scripts/import_packs.py` verifies a detached signature (`sigstore`-compatible or HMAC with a deploy-time secret) over the JSON archive. Unsigned imports are refused. Every imported row records `imported_from = sha256(archive)`. (`AC-PRECOMP-SEC-5`)
6. **Image safety inheritance.** When a character or media asset is reused across topics, its safety verdict carries — but if any flag-driven re-evaluation lowers its score below `τ_pass`, *every* pack referencing it is auto-quarantined. (`AC-PRECOMP-SEC-6`)
7. **Flag endpoint anti-abuse:**
   - Per-IP-hash rate-limit (already in §21.13: 5/min, 30/day).
   - **Honeypot reason code** (`reason_code: "test"`) silently dropped — used by malicious linters / scanners.
   - Server stores the *count of distinct flagged target_ids per ip_hash per day*; if > 50 in a day, future flags from that hash are accepted but **shadow-discarded** (returned 202, not counted toward quarantine threshold). (`AC-PRECOMP-SEC-7`)
8. **Right-to-be-forgotten path.** A `POST /admin/users/forget` accepting `ip_hash` zeroes that hash's flag rows (preserving counts for already-quarantined packs but losing the linkage). Documented in privacy policy. (`AC-PRECOMP-SEC-8`)
9. **Secrets hygiene for `FLAG_HMAC_SECRET` and `OPERATOR_TOKEN`.** Required to be ≥ 32 random bytes, fail-closed on startup if missing in production env, never logged (existing `_redact_in_str` covers them via key-name allowlist update). (`AC-PRECOMP-SEC-9`)

#### Review 11 — Cost management, second pass

*What was missing.* §21.17 set a daily budget but didn't specify (a) batching strategies, (b) embedding cost containment, (c) opportunistic off-peak generation, (d) cost attribution per topic.

*Decisions:*
1. **Embedding cache.** All text embeddings (topic_aliases, synopses, characters, questions) are deduped on `text_hash` before the embedding API call; cached results live in `embeddings_cache(text_hash, vector, model_version, created_at)`. New text → embed once, ever. (`AC-PRECOMP-COST-1`)
2. **Evaluator batching.** Within a single build attempt, all character evaluations are sent in **one** evaluator call returning `[{character_id, score, blocking_reasons}, ...]`. Same for the question set. Reduces evaluator call count by ~6×. (`AC-PRECOMP-COST-2`)
3. **Generation batching where the model supports it.** A single LLM call generates *all* baseline questions in a structured array; constituent scoring is still per-question via the batched evaluator. (`AC-PRECOMP-COST-3`)
4. **Cost attribution.** `topic_packs.cost_cents` and `precompute_jobs.cost_cents` are populated from per-call `prompt_tokens × $/Mtok + completion_tokens × $/Mtok + image_count × $/img`. A view `v_topic_cost_30d` is exposed at `/admin/precompute/cost`. (`AC-PRECOMP-COST-4`)
5. **Off-peak scheduling.** Build worker has two cost knobs: `daytime_concurrency` (low, default 1), `offpeak_concurrency` (default 4) and `offpeak_window_utc: "02:00-08:00"`. Backfills happen overnight. (`AC-PRECOMP-COST-5`)
6. **Cost-aware tier escalation.** A worker MUST NOT escalate to Tier-3 (web search) if today's spend ≥ 75% of `daily_budget_usd`; it instead defers the job to the next day. Cheap-only fallback is allowed but the `pack.evaluator_score` must clear `τ_pass` without escalation. (`AC-PRECOMP-COST-6`)
7. **Image cost rules:**
   - One synopsis image + one image per character + **zero** per-question images by default. Per-topic config can enable per-question images.
   - FAL prompt deduplication (`prompt_hash`) means thematically-shared images (e.g. background-only "fantasy library") cost once across many topics. (`AC-PRECOMP-COST-7`)
8. **Live-session promotion is opportunistic.** A successfully completed live quiz only enqueues a build job if the topic's `current_pack_id IS NULL` AND `topic.popularity_rank < 5000` (i.e. likely-to-recur). Prevents the long-tail from burning budget. (`AC-PRECOMP-COST-8`)
9. **Ballpark expected $:**
   - Cheap-tier full pack (no images): ~$0.02–$0.05.
   - + 6 FAL images at $0.04 each: ~$0.24 → **~$0.30/pack typical**.
   - 100 packs/day × $0.30 = $30/day; with the 75%-cap rule, hard ceiling at ~$25/day. Annualised ~$9k for ongoing, ~$300 one-time for the starter 1k catalog.

#### Review 12 — Overall design (with Azure migration as a first-class concern)

*What was missing.* §21.10 was a one-paragraph plan for "interim, then Azure". This is too important — image persistence is one of the user's stated worries — so we promote it to a full sub-design.

*Decisions:*

**12.1 — Storage abstraction is the migration's load-bearing wall.** The FE never knows where bytes live. Internally:

```python
# Single resolver in app/services/media_resolver.py
def resolve_media_url(asset: MediaAsset) -> str:
    if asset.storage_provider == "fal":      return asset.storage_uri      # Phase 0
    if asset.storage_provider == "local":    return f"/api/v1/media/{asset.id}"  # Phase 0.5
    if asset.storage_provider == "blob":     return asset.storage_uri      # Phase 1
    if asset.storage_provider == "blob+cdn": return asset.storage_uri      # Phase 1+CDN
```

**12.2 — Phase migration ladder (each step independently shippable):**

| Phase | Storage | FE-visible URL pattern | Trigger to advance |
|---|---|---|---|
| 0 | FAL CDN URL stored as-is | `https://fal.media/.../*.png` | Always on |
| 0.5 | FAL → local Postgres BYTEA → backend route | `/api/v1/media/{asset_id}` | When `expires_at < now()+7d` for any hot asset |
| 1 | Azure Blob (container `quizzical-media`, hot tier, immutable policy) | `https://<account>.blob.core.windows.net/quizzical-media/{prefix}/{hash}.{ext}` | When `BLOB_BASE_URL` env is set + smoke passes |
| 1+CDN | Front Door / Azure CDN in front of blob | `https://media.quizzicalai.com/{prefix}/{hash}.{ext}` | When DNS + cert provisioned |

**12.3 — Dual-write transition (Phase 0.5 → 1).**
- For 7 days after `BLOB_BASE_URL` is set, the rehost worker writes to BOTH the local BYTEA store AND blob; the resolver prefers blob, falling back to local on 404.
- After the dual-write window, a one-shot migrator copies remaining `local` assets to blob and flips `storage_provider`.
- Local BYTEA column is then dropped.
- All flips are gated by `media_assets.content_hash` integrity check (re-read the bytes, recompute hash, refuse on mismatch). (`AC-PRECOMP-MIGR-1..3`)

**12.4 — Content-addressed pathing for blob.** Path = `{hash[0:2]}/{hash[2:4]}/{hash}.{ext}`. Two-deep prefix avoids hot partitions in Azure Blob; immutable policy prevents accidental overwrites. (`AC-PRECOMP-MIGR-4`)

**12.5 — Signed URLs vs public-read.** Container is **public-read** (no PII in images) so we don't manage SAS tokens or rotate keys; this also lets us put the CDN in front for free. The `Cache-Control: public, max-age=31536000, immutable` is set at upload time (blob property), so the CDN inherits it. (`AC-PRECOMP-MIGR-5`)

**12.6 — Pack archive migration.** Pack content (synopses/characters/questions/sets) is migrated by `scripts/export_packs.py` → signed JSON archive → `scripts/import_packs.py`. Image bytes are NOT inside the archive (too big); they're referenced by `content_hash`. The importer resolves each `content_hash`:
1. Already in destination `media_assets`? Skip.
2. Source `storage_uri` reachable? Stream-download → upload → set storage_provider.
3. Otherwise, mark asset `pending_rehost` and let the worker handle it asynchronously.

This means the dev-to-prod migration is **idempotent and resumable**. (`AC-PRECOMP-MIGR-6`)

**12.7 — Disaster recovery for images.** Because every asset is content-addressed:
- Bytes lost? Re-derive from FAL by re-running the original `prompt_hash` (provenance preserved in `media_assets.prompt_payload JSONB`). Vision evaluator must re-pass before the new bytes are accepted (the *prompt* is the spec, not the previous bytes). (`AC-PRECOMP-MIGR-7`)
- Database lost but images intact? Replay `import_packs.py` from the latest archive in object storage.
- Both lost? Replay from the bundled starter catalog + telemetry-enqueued seeds.

**12.8 — Operating costs of Azure storage.** Hot tier blob: ~$0.018/GB/month. ~10k images × ~150 KB = ~1.5 GB → **<$0.05/month**. Negligible vs LLM/FAL costs. CDN egress at ~$0.05/GB; 10k MAU × 6 images × 150 KB = ~9 GB/month → ~$0.45/month at the start. The migration is cheap and forward-only. (`AC-PRECOMP-MIGR-8`)

**12.9 — Evaluator and operator UX during Azure transition.** `media_assets.storage_provider` is also surfaced in `/admin/precompute/jobs` and the operator review UI, so a human can see at a glance which assets are in flight. Quarantine, flagging, and pack lookup all work uniformly across providers — they only see `MediaAsset.id`. (`AC-PRECOMP-MIGR-9`)

**12.10 — Holistic observation: the design is now a small platform, not a feature.** The combination of {versioned packs, content-addressed media, signed import archives, evaluator A/B harness, immutable audit log, telemetry-driven seeding} is the right shape for a system that will hold *thousands* of validated topics for years. The core invariant that keeps the design elegant is: **everything user-visible is read out of `topics → topic_packs → mv_topic_pack_resolved`; everything else is bookkeeping**. As long as that materialised view remains the single read source for the hot path, we can swap evaluators, providers, storage backends, even prompt families, without touching `/quiz/start` or `/quiz/status`.

---

### 21.19 Acceptance Criteria — Iteration 2 (consolidated)

The following ACs were introduced by §21.18 and complement (do not replace) the §21.13 ACs.

**Objectives**
- `AC-PRECOMP-OBJ-1`: `/quiz/start` returns `pack_status ∈ {hit, miss, degraded}` plus a placeholder synopsis on miss; FE first paint is identical for both paths.
- `AC-PRECOMP-OBJ-2`: A starter catalog of bundled packs is imported on first deploy when the destination DB has zero published packs.
- `AC-PRECOMP-OBJ-3`: `/healthz/precompute` returns hit_rate / miss_rate / top_misses for the trailing 24 h, gated by `OPERATOR_TOKEN`.
- `AC-PRECOMP-OBJ-4`: A per-IP-hash recent-topics cache exists; never counts toward auth or rate-limit decisions.

**Performance**
- `AC-PRECOMP-PERF-1`: `mv_topic_pack_resolved` is refreshed `CONCURRENTLY` inside `publish(pack_id)` and read by the hot path.
- `AC-PRECOMP-PERF-2`: Redis pack cache `tk:pack:{topic_id}` uses a fill lock (SETNX) to prevent thundering herd; TTL 1 h; invalidated transactionally.
- `AC-PRECOMP-PERF-3`: HTTP `103 Early Hints` is emitted for HIT paths preloading expected media URIs (best-effort; harmless if proxy strips).
- `AC-PRECOMP-PERF-4`: `/api/v1/media/{asset_id}` sets `Cache-Control: public, max-age=31536000, immutable` and an ETag derived from `content_hash`.
- `AC-PRECOMP-PERF-5`: No model call occurs on the read path; embeddings used by lookup are precomputed at write time.
- `AC-PRECOMP-PERF-6`: Hot characters (referenced by > 200 packs) are pinned in Redis for icon resolution.

**HIT — full short-circuit (`/quiz/start` skips the LangGraph agent)**
- `AC-PRECOMP-HIT-1`: When the precompute resolver returns a HIT and the resolved pack carries both a synopsis and at least one referenced character row, `/quiz/start` synthesises the GraphState and returns the `synopsis` payload + character cards directly from the pack — the LangGraph agent is NOT invoked. A `precompute.start.short_circuit` structlog event is emitted with `quiz_id`, `pack_id`, `topic_id`, and character count.
- `AC-PRECOMP-HIT-2`: When the resolver hits a pack that only carries a synopsis (no character rows wired through `character_set.composition.character_ids`), `/quiz/start` falls through to the live agent path. A `precompute.start.short_circuit.skip_no_content` event is emitted; no exception is raised.
- `AC-PRECOMP-HIT-3`: Any exception inside the short-circuit path is caught, logged as `precompute.start.short_circuit_error`, and the request continues through the live agent path so the user always gets an experience (fail-open).
- `AC-PRECOMP-HIT-4`: On short-circuit, image generation is still scheduled in the background via the existing FAL pipeline (`_schedule_image_jobs_safe`) so character icons appear within the normal latency budget.
- `AC-PRECOMP-HIT-5`: The persisted `session_history` row on a short-circuit carries `agent_plan.source = "precompute"` and `agent_plan.pack_id` set to the resolved pack id (provenance for downstream tooling and replay).

**Operator override — re-import of upgraded packs**
- `AC-PRECOMP-IMPORT-1`: `POST /api/v1/admin/precompute/import-starter-packs?force_upgrade=true` bypasses the global "first-deploy only" gate so an already-seeded environment can ingest a new archive version. Per-pack idempotency on `(topic_id, version)` still prevents duplicate inserts; existing rows of the same version are skipped.
- `AC-PRECOMP-IMPORT-2`: When a pack archive entry includes inline `characters: [{name, short_description, profile_text}]` plus `cs_composition.character_keys`, the importer upserts each character by canonical name (creating new rows on first sight, reusing existing ones idempotently) and rewrites the persisted `character_set.composition` to `{"character_ids": [<uuid>, …]}` so the read-path hydrator can resolve the cards on `/quiz/start`.
- `AC-PRECOMP-IMPORT-3`: When a v3+ archive entry includes inline `questions: [{text_hash, text, options, kind}]` plus `baseline_question_set.composition.question_keys`, the importer upserts each question by `text_hash` into the `questions` table (kind=`baseline`) and rewrites the persisted `baseline_question_set.composition` to `{"question_ids": [<uuid>, …]}`. Re-importing the same archive is a no-op (idempotent on `text_hash`).
- `AC-PRECOMP-IMPORT-4`: When a v3+ archive entry includes optional `characters[].image_url`, the importer writes that URL to `characters.image_url` on first creation. For pre-existing Character rows, the URL is backfilled only when `image_url IS NULL` (curated values are never overwritten).

**HIT — baseline-question short-circuit (`/quiz/proceed` skips the LangGraph agent)**
- `AC-PRECOMP-PROCEED-1`: When `/quiz/start` populated state from a v3+ pack (i.e. `agent_plan.source='precompute'` AND `baseline_ready=True` AND non-empty `generated_questions`), the first call to `/quiz/proceed` schedules `_persist_baseline_questions` directly and returns `202` without invoking the LangGraph agent. A `precompute.proceed.short_circuit` structlog event fires with `quiz_id`, `pack_id`, and `baseline_count`.
- `AC-PRECOMP-PROCEED-2`: Sessions whose state lacks `agent_plan.source='precompute'` continue to schedule `run_agent_in_background` exactly as before — the short-circuit is opt-in by precompute provenance, never reached by live-agent sessions even if `baseline_ready` is set externally.
- `AC-PRECOMP-PROCEED-3`: Any exception inside the proceed short-circuit path is caught, logged as `precompute.proceed.short_circuit_error`, and the request falls through to `run_agent_in_background` (fail-open).

**Quality**
- `AC-PRECOMP-QUAL-1`: Both generator and evaluator prompts are versioned (`semver` + `sha256`) and stored in `model_provenance`.
- `AC-PRECOMP-QUAL-2`: Two-judge consensus is required for factual artefacts; a 2-point divergence triggers Tier-3 web-search evaluation.
- `AC-PRECOMP-QUAL-3`: Evaluator promotion requires strict improvement in precision *and* recall on a held-out golden set.
- `AC-PRECOMP-QUAL-4`: A weekly self-consistency probe over a 5% sample triggers `evaluator_drift_paused` if pass-rate drops > 10 pp.
- `AC-PRECOMP-QUAL-5`: Evaluator output is `{score, blocking_reasons[], non_blocking_notes[]}`; any blocking reason rejects regardless of score.
- `AC-PRECOMP-QUAL-6`: Tier-3 evaluator output MUST include `sources: [{url, snippet}]` for each blocking reason.
- `AC-PRECOMP-QUAL-7`: Cross-pack consistency check rejects builds whose new character `profile_text` embedding differs from the canonical version by cosine < 0.85.

**Security**
- `AC-PRECOMP-SEC-1`: Outbound HTTP client deny-lists private/loopback/link-local CIDRs; pins resolved IP per request.
- `AC-PRECOMP-SEC-2`: Web-retrieved content is wrapped in `<retrieved>` tags; system prompts forbid honoring instructions inside.
- `AC-PRECOMP-SEC-3`: `/topics/suggest` rejects q.length<2, returns ≤ 8 items, rate-limited 60/min/IP, prefix+alias-only matching.
- `AC-PRECOMP-SEC-4`: Production promote/rollback requires `OPERATOR_TOKEN` AND `X-Operator-2FA`.
- `AC-PRECOMP-SEC-5`: Pack archive imports verify a detached signature; unsigned archives refused.
- `AC-PRECOMP-SEC-5a`: `POST /admin/precompute/import` requires `Authorization: Bearer ${OPERATOR_TOKEN}` AND `X-Archive-Signature` (hex HMAC-SHA256). Missing header → 401; secret missing/<32 bytes → 503; empty body → 400; invalid signature → 401. Returns `{packs_inserted, packs_skipped, skipped_db_not_empty}` (HTTP 200) and writes one `audit_log` row with `target_kind="archive"`, `target_id="starter-pack-archive"`.
- `AC-PRECOMP-SEC-6`: Lowering an asset/character below `τ_pass` auto-quarantines every pack that references it.
- `AC-PRECOMP-SEC-7`: Flag honeypot codes silently dropped; > 50 distinct-target flags/day from one ip_hash → shadow-discard.
- `AC-PRECOMP-SEC-8`: `POST /admin/users/forget` zeroes flag-row linkage for a given `ip_hash`.
- `AC-PRECOMP-SEC-9`: `FLAG_HMAC_SECRET` and `OPERATOR_TOKEN` ≥ 32 random bytes; fail-closed at startup in production if absent.

**Cost**
- `AC-PRECOMP-COST-1`: All embeddings are deduped on `text_hash` via `embeddings_cache`.
- `AC-PRECOMP-COST-2`: Evaluator scores all characters / all questions in a batched call per build attempt.
- `AC-PRECOMP-COST-3`: Generator produces all baseline questions in one call.
- `AC-PRECOMP-COST-4`: `topic_packs.cost_cents` and `v_topic_cost_30d` reflect actual token + image spend; surfaced under `/admin/precompute/cost`.
- `AC-PRECOMP-COST-5`: Worker honours `daytime_concurrency` and `offpeak_concurrency` with `offpeak_window_utc`.
- `AC-PRECOMP-COST-6`: Tier-3 escalation is suppressed when today's spend ≥ 75% of `daily_budget_usd`.
- `AC-PRECOMP-COST-7`: Per-question images default off; opt-in per topic.
- `AC-PRECOMP-COST-8`: Live-session-driven enqueue requires `current_pack_id IS NULL` AND `topic.popularity_rank < 5000`.

**Migration / Azure**
- `AC-PRECOMP-MIGR-1`: Phase 0.5 → 1 dual-write window of 7 days; resolver prefers blob, falls back to local.
- `AC-PRECOMP-MIGR-2`: One-shot migrator runs after dual-write; `bytes_blob` column dropped only after migrator success.
- `AC-PRECOMP-MIGR-3`: Every storage provider flip re-reads bytes and re-verifies `content_hash`; mismatch refuses the flip.
- `AC-PRECOMP-MIGR-4`: Blob path is `{hash[0:2]}/{hash[2:4]}/{hash}.{ext}`; container has immutable policy.
- `AC-PRECOMP-MIGR-5`: Container is public-read; `Cache-Control: public, max-age=31536000, immutable` set as a blob property.
- `AC-PRECOMP-MIGR-6`: `import_packs.py` is idempotent on `content_hash` / `composition_hash`; image rehosting is async via `pending_rehost`.
- `AC-PRECOMP-MIGR-6a`: Each imported topic has `topic.current_pack_id` set to the inserted pack so alias / slug lookup HITs immediately resolve. Each `aliases[]` entry produces an idempotent `topic_aliases` row keyed on `canonical_key_for_name(alias)`; existing rows are preserved.
- `AC-PRECOMP-MIGR-7`: An asset whose bytes are lost can be re-derived from `media_assets.prompt_payload`; vision evaluator must re-pass before reuse.
- `AC-PRECOMP-MIGR-8`: Operating cost telemetry includes blob storage GB and CDN egress GB; surfaced alongside LLM/FAL spend.
- `AC-PRECOMP-MIGR-9`: `storage_provider` is visible in operator UIs and `/admin/precompute/jobs`.

---

*End of specification. This document is the source of truth for the Quizzical backend.*
