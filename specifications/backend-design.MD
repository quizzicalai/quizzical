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
│   │   ├── canonical_sets.py    # Hard-coded archetype/character canonical sets
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
│   └── character_concurrency: int | None = None  # defaults to min(4, len(archetypes)) at runtime
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
1. Batch attempt: call `tool_draft_character_profiles` (all archetypes at once) within `asyncio.wait_for(timeout=per_call_timeout_s)`; skipped if `len(archetypes) <= 1`
2. Fill missing: for archetypes not returned by batch (or if batch failed), call individual `draft_character_profile` concurrently
3. Concurrency bounded by `min(4, len(archetypes))` unless `settings.quiz.character_concurrency` is explicitly set
4. Per-item retries: up to `settings.agent.max_retries` attempts; sleep `0.5 + 0.5 * attempt` seconds between retries
5. Name-locks each result (character mapped by name)
6. Write to state: `generated_characters` (list of CharacterProfile dicts)

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

## 10. Observability

### Structured Logging

- Library: `structlog` with `stdlib` bridge
- Processors: timestamp, log level, caller info, redaction, sampling, JSON renderer (prod) / console renderer (dev)
- `LLM_SLOW_MS` env var (default `2000`): LLM calls exceeding this threshold emit a `slow_llm_call` warning
- `trace_id` propagated through agent state and attached to all log events within a quiz session
- Request `X-Trace-ID` / `X-Request-ID` headers echoed in responses and attached to log context

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

*End of specification. This document is the source of truth for the Quizzical backend.*
