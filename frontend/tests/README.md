# Tests still needed:

### E2E (Playwright) — 4 specs

* `tests/e2e/turnstile.spec.ts`

  * Enabled: token acquired before `/start`.
  * Invalid/expired token: start blocked with inline error.
* `tests/e2e/errors.spec.ts`

  * `/start` or question fetch returns 500/429 → global error shown; **Retry** restores flow.
* `tests/e2e/routing-session.spec.ts`

  * Deep-link to `/quiz` without session → redirected to landing.
  * Refresh mid-quiz → progress resumes from `utils/session`.
* `tests/e2e/final-basic.spec.ts`

  * Final/Result page renders from fixture; primary CTA(s) visible (no  layout break).

### Contract / Schema (Vitest) — 2 specs

* `src/schemas/quiz.spec.ts` & `src/schemas/status.spec.ts`

  * Valid fixture passes; missing/unknown enum fails with readable error.
* `src/services/apiService.contract.spec.ts`

  * `/start`, `/question`, `/answer`, `/result`, `/status` responses are parsed by Zod; missing required fields are rejected.

### Config fallback (Vitest) — 1 spec

* `src/services/configService.spec.ts` (add one case)

  * Remote config fetch fails → app uses `src/config/defaultAppConfig.ts`.

### A11y smoke (Playwright + axe) — 1 spec

* `tests/e2e/a11y.spec.ts`

  * No critical violations on Landing, Question, Final, Error pages.

Here’s the **minimum test set** to prove our frontend and backend “talk” correctly, without bloat. It’s three layers: one FE contract suite, one BE integration suite, and two E2E smokes.

### 1) Frontend → API contract (single spec)

**Goal:** FE only accepts/produces shapes the BE actually serves.
**Tool:** Vitest + Zod (already in repo) + MSW/HAR fixtures.
**File:** `src/services/apiService.contract.spec.ts`
**Checks (one valid + one invalid per endpoint):**

* `POST /start`, `GET /question`, `POST /answer`, `GET /result`, `GET /status`:

  * Valid sample parses with Zod (uses `src/schemas/*`).
  * Missing/unknown enum → Zod fails with readable error.
* Request headers include Turnstile token when enabled.

### 2) Backend service-boundary integration (single suite)

**Goal:** BE returns correct codes/shapes and is callable by the FE.
**Tool:** Backend test runner (e.g., Jest) + supertest/Testcontainers (or docker-compose).
**File:** `backend/test/api.integration.spec.ts`
**Checks (happy + 1 negative total is enough):**

* Happy path for each endpoint (same five as above) returns 2xx and minimal required fields.
* **One representative negative**: invalid payload → `400` with JSON error body.
* **CORS preflight**: `OPTIONS /{any}` allows FE origin and headers (esp. Turnstile/Authorization).

### 3) End-to-end smokes (two tiny specs)

**Goal:** Real UI → real API round-trip works and fails gracefully.
**Tool:** Playwright. Target staging/ephemeral env.

1. `tests/e2e/comm-happy.spec.ts`

   * Start quiz → fetch question → submit answer → reach results (assert key UI text).
2. `tests/e2e/comm-failure.spec.ts`

   * Force API `500` (route interception or a fault flag) → global error is shown and Retry recovers.
