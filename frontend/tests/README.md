# Testing README — Frontend (Vite + React + Vitest + Playwright + MSW)

> **Purpose:** Give an AI coding assistant enough context to **generate high-quality tests** for this repo.
> **Scope:** Our stack, tools, file/layout conventions, the order we build tests, and **requirements** (completeness, edge cases, practices).

---

## 1) App & repo context

* **App type:** React app in `frontend/` using **Vite**.
* **Key tech:** React 19, React Router, Zustand, Zod, Tailwind, Vite.
* **Folders (under `frontend/src/`):**
  `assets/ • components/(common|layout|quiz|result) • config/ • context/ • hooks/ • mocks/ • pages/ • router/ • schema/ • services/ • store/ • styles/ • types/ • utils/ • App.tsx • main.tsx`.

---

## 2) Testing stack (what we use & why)

* **Unit & Component tests:** **Vitest** + **React Testing Library (RTL)** in **jsdom**.

  * Vitest is Vite-native, fast, Jest-style APIs. Configure `test.environment = 'jsdom'`. ([Vitest][1])
  * RTL drives tests by **user-facing queries** (role/text), discouraging implementation details. ([Testing Library][2])
* **Network mocking for unit/integration:** **MSW (Mock Service Worker)** with `setupServer` in Node test env. ([Mock Service Worker][3])
* **End-to-end (E2E):** **Playwright Test** (Chromium/Firefox/WebKit) with `webServer` to boot Vite before tests; Trace/Video on failure for triage. ([Playwright][4])
* **State testing:** **Zustand** store tests + component tests that consume it (per Zustand testing guide). ([Zustand Documentation][5])
* **Data contracts:** **Zod** schemas with both `parse` and `safeParse` paths covered. ([Zod][6])

---

## 3) Directory & file conventions

```
frontend/
  tests/
    setup.ts            # Vitest global setup (starts/stops MSW)
    msw/
      handlers.ts
      server.ts
  tests/e2e/
    smoke.spec.ts       # Playwright E2E
  src/
    **/*.test.ts        # units (utils, services, schema, store)
    **/*.test.tsx       # components/hooks/pages as applicable
```

* **Co-locate** tests next to code (preferred), keep shared helpers in `tests/`.
* Vitest will run `*.test.[tj]s?(x)` with `environment: 'jsdom'` when DOM is needed. ([Vitest][1])

---

## 4) The build order (test pyramid)

> Build from **broad guardrails → depth on logic → UX flows → edges**.

### Milestone A — E2E “smoke” (Playwright)

* App boots (Vite server via `webServer`), main nav works, core “quiz” happy path.
* Rationale: validates wiring and routes early; prevents writing units on a broken app. ([Playwright][4])

### Milestone B — Fast unit tests (pure code)

* `utils/` (pure functions, boundaries).
* `schema/` (Zod): valid vs invalid using `parse`/**`safeParse`**; include async if any. ([Zod][6])

### Milestone C — Components (RTL)

* `components/(common|quiz|result)` & critical UI: render, interact, loading/empty/error states; query by role/text per RTL guidance. ([Testing Library][7])

### Milestone D — Integration (+ MSW)

* UI + `services/`: intercept `fetch` with MSW; test success, 4xx/5xx, timeouts/retries. Wire MSW in `tests/setup.ts` using `setupServer`. ([Mock Service Worker][3])

### Milestone E — Routing & pages

* Prefer **integration/E2E** for route behavior (React Router guidance and RTL examples). Use MemoryRouter/`createRoutesStub` when unit-scoping routed components. ([React Router][8])

### Milestone F — State/store (Zustand)

* Unit test store (initial state, actions/selectors) and a couple of components consuming it (with RTL). ([Zustand Documentation][5])

---

## 5) How to create the tests (templates)

### 5.1 Vitest config (snippet)

* In `frontend/vitest.config.ts` (or `vite.config`’s `test` block):

```ts
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    setupFiles: ['./tests/setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
    css: true,
    coverage: { reporter: ['text', 'html'], all: true, include: ['src/**/*'] }
  }
})
```

Vitest `jsdom` env & setup file are the important bits. ([Vitest][1])

### 5.2 RTL component test (pattern)

* `src/components/quiz/StartButton.test.tsx`

```tsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import StartButton from './StartButton'

test('enables when prerequisites are met', async () => {
  const user = userEvent.setup()
  render(<StartButton canStart={false} />)
  expect(screen.getByRole('button', { name: /start/i })).toBeDisabled()
  render(<StartButton canStart />)
  const btn = screen.getByRole('button', { name: /start/i })
  await user.click(btn)
  // assert side-effect (emit/event/route) here
})
```

Use **role/name** queries; avoid internal state assertions. ([Testing Library][7])

### 5.3 MSW wiring (global)

* `tests/msw/server.ts`

```ts
import { setupServer } from 'msw/node'
import { handlers } from './handlers'
export const server = setupServer(...handlers)
```

* `tests/setup.ts`

```ts
import '@testing-library/jest-dom'
import { server } from './msw/server'
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }))
afterEach(() => server.resetHandlers())
afterAll(() => server.close())
```

MSW `setupServer` is the Node test entrypoint. ([Mock Service Worker][3])

### 5.4 Service/integration test with MSW

```ts
import { http, HttpResponse } from 'msw'
import { server } from '../../tests/msw/server'
import { fetchQuiz } from './quizService'

test('returns quiz data', async () => {
  server.use(http.get('/api/quiz', () => HttpResponse.json({ items: [] })))
  const data = await fetchQuiz()
  expect(data.items).toEqual([])
})
```

(Handlers live in `tests/msw/handlers.ts` as reusable defaults.) ([Mock Service Worker][9])

### 5.5 Zod schema tests

```ts
import { QuizSchema } from './quizSchema'

test('accepts valid shape', () => {
  const valid = { title: 'Q1', questions: [] }
  expect(QuizSchema.parse(valid)).toBeTruthy()
})

test('rejects invalid shape with safeParse', () => {
  const res = QuizSchema.safeParse({ title: 123 })
  expect(res.success).toBe(false)
})
```

Cover `parse` (throws) and `safeParse` (result object). ([Zod][6])

### 5.6 Zustand store tests

```ts
import { createStore } from 'zustand'
import { useQuizStore, createQuizStore } from './quizStore'

test('actions update state', () => {
  const store = createQuizStore() // factory that wraps create()
  store.getState().addAnswer({ id: 'q1', value: 'A' })
  expect(store.getState().answers).toHaveLength(1)
})
```

Zustand recommends testing stores directly and using RTL for components that consume them. ([Zustand Documentation][5])

### 5.7 Playwright E2E (smoke)

* `frontend/playwright.config.ts` should launch Vite:

```ts
import { defineConfig, devices } from '@playwright/test'
export default defineConfig({
  testDir: 'tests/e2e',
  use: { baseURL: 'http://localhost:5173', trace: 'on-first-retry', video: 'retain-on-failure' },
  webServer: [{ command: 'npm run dev', url: 'http://localhost:5173', reuseExistingServer: true, timeout: 120000 }],
  projects: [
    { name: 'chromium', use: devices['Desktop Chrome'] },
    { name: 'firefox',  use: devices['Desktop Firefox'] },
    { name: 'webkit',   use: devices['Desktop Safari'] }
  ]
})
```

Playwright’s **`webServer`** starts the dev server before tests. ([Playwright][4])

* `tests/e2e/smoke.spec.ts`

```ts
import { test, expect } from '@playwright/test'
test('app loads and navigates', async ({ page }) => {
  await page.goto('/') // uses baseURL
  await expect(page).toHaveTitle(/frontend/i)
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
  await page.getByRole('link', { name: /quiz/i }).click()
  await expect(page).toHaveURL(/quiz/)
})
```

---

## 6) What to test (requirements checklist)

### A) Completeness (per layer)

* **E2E:**

  * **Happy paths:** app boots, routing works, quiz flow from start → answers → result.
  * **Unhappy paths:** failed fetch shows error UI; retry restores flow; auth-guard redirects (if applicable).
  * **Cross-browser:** keep projects for Chromium/Firefox/WebKit. ([Playwright][10])
* **Integration (UI + services via MSW):**

  * Loading/empty/error states, pagination, retries/backoff, invalid JSON → schema failure UI. ([Mock Service Worker][3])
* **Components (RTL):**

  * Visibility, enable/disable, aria-labels, keyboard interaction where relevant; no reliance on internal state/instance. ([Testing Library][7])
* **Units:**

  * `utils/` edge cases; `schema/` with `parse`/`safeParse`; `store/` actions & selectors. ([Zod][6])

### B) Edge cases (hit these explicitly)

* Empty lists / one item / max items.
* Network: 4xx, 401/403, 5xx, slow response/timeout, malformed payload (Zod should fail).
* State: invalid transitions, repeated actions, resets.
* Forms: invalid inputs, debounce/race conditions, keyboard navigation.

### C) Best practices

* **Prefer user-level queries**: `getByRole`, `getByLabelText`, `getByText` (avoid `data-testid` unless necessary). ([Testing Library][11])
* **No implementation-detail assertions** (internal state, private methods). ([Testing Library][7])
* **Mock network at the boundary** with MSW; don’t stub inside components. Start/stop server in the global setup. ([Mock Service Worker][3])
* **Schema-first contracts**: all service responses validated via Zod; tests assert both accepted and rejected inputs. ([Zod][6])
* **Route tests**: integration/E2E for full routing, MemoryRouter or `createRoutesStub` for small unitized cases. ([React Router][8])
* **Zustand**: test store logic directly; use RTL to test components consuming the store. ([Zustand Documentation][5])

---

## 7) Running the suite

* **Unit/Component (Vitest):** `npm test` / `npm run test:run` / `npm run test:coverage`
  Set `environment: 'jsdom'` in config. ([Vitest][1])
* **E2E (Playwright):** `npx playwright test` / `--ui` / `--project=webkit`
  Uses `webServer` to start Vite automatically. ([Playwright][4])

---

## 8) CI guidance (high level)

* Job 1: `lint` → `vitest run --coverage` (upload HTML/text reports).
* Job 2 (matrix on browser): `npx playwright install --with-deps` → `playwright test` (produce trace/video). ([Playwright][10])

---

## 9) What the AI should infer when generating tests

1. **Pick the smallest layer** that proves the behavior (unit > component > integration > E2E).
2. **Use RTL queries in this order**: role → label → placeholder → text → testid (last). ([Testing Library][11])
3. **Stub network with MSW** (never real calls). Provide default handlers + per-test overrides. ([Mock Service Worker][3])
4. **Validate data** using **Zod** both for success and failure (`parse` vs `safeParse`). ([Zod][6])
5. **Zustand**: test store APIs and selector behavior; for components, ensure state updates happen via user interactions. ([Zustand Documentation][5])
6. **Routes**: prefer integration/E2E; only unit-stub router context when absolutely necessary. ([React Router][8])
7. **Artifacts**: in E2E, keep trace/video/screenshot on failure for triage. ([Playwright][10])

---

## 10) Minimal “starter” tasks for the agent

* Create `tests/e2e/smoke.spec.ts` (boot, h1 visible, navigate Quiz → Result). **Playwright**. ([Playwright][4])
* Add `tests/setup.ts` and base `tests/msw/handlers.ts` + `server.ts`. **MSW**. ([Mock Service Worker][3])
* Add `src/schema/quizSchema.test.ts` covering valid/invalid with `parse`/`safeParse`. **Zod**. ([Zod][6])
* Add `src/utils/score.test.ts` for scoring logic edge cases. **Vitest**. ([Vitest][1])
* Add `src/components/quiz/StartButton.test.tsx` using RTL queries by role & interactions. **RTL**. ([Testing Library][7])
* Add `src/store/quizStore.test.ts` for initial state & actions. **Zustand**. ([Zustand Documentation][5])

---

### References

* Vitest environments/config & jsdom. ([Vitest][1])
* Testing Library: intro, guiding principles, query priority. ([Testing Library][12])
* MSW: Node setup & `setupServer`. ([Mock Service Worker][3])
* Playwright: `webServer` & test configuration. ([Playwright][4])
* React Router testing guidance & RTL example. ([React Router][8])
* Zustand testing guide. ([Zustand Documentation][5])
* Zod basics (`parse`/`safeParse`). ([Zod][6])

---

If you’d like, I can drop this into `frontend/TESTING_README.md` and scaffold the initial spec files mentioned in “starter tasks.”

[1]: https://vitest.dev/guide/environment?utm_source=chatgpt.com "Test Environment | Guide | Vitest"
[2]: https://testing-library.com/docs/guiding-principles/?utm_source=chatgpt.com "Guiding Principles - Testing Library"
[3]: https://mswjs.io/docs/integrations/node?utm_source=chatgpt.com "Node.js integration - Mock Service Worker"
[4]: https://playwright.dev/docs/test-webserver?utm_source=chatgpt.com "Web server | Playwright"
[5]: https://zustand.docs.pmnd.rs/guides/testing?utm_source=chatgpt.com "Testing - Zustand"
[6]: https://zod.dev/basics?utm_source=chatgpt.com "Basic usage | Zod"
[7]: https://testing-library.com/docs/?utm_source=chatgpt.com "Introduction - Testing Library"
[8]: https://reactrouter.com/start/framework/testing?utm_source=chatgpt.com "Testing | React Router"
[9]: https://mswjs.io/docs/api/setup-server?utm_source=chatgpt.com "setupServer - Mock Service Worker"
[10]: https://playwright.dev/docs/test-configuration?utm_source=chatgpt.com "Test configuration | Playwright"
[11]: https://testing-library.com/docs/queries/about/?utm_source=chatgpt.com "About Queries - Testing Library"
[12]: https://testing-library.com/docs/react-testing-library/intro/?utm_source=chatgpt.com "React Testing Library"
