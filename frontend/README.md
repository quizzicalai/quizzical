# Quizzical Frontend

The Quizzical frontend is a React single-page application that drives quiz creation, polling-based quiz progression, result sharing, and feedback capture for the Quizzical product.

It is not a static marketing site. The application bootstraps runtime configuration from the backend, injects theme tokens into CSS variables, coordinates a guarded quiz session lifecycle through Zustand state, and renders a route-based experience for landing, quiz, result, and informational pages.

## Current Responsibilities

- Load runtime application configuration from the backend `/config` endpoint and merge it with local defaults.
- Render the landing experience, topic input, and Turnstile-gated quiz start flow.
- Drive the quiz lifecycle from synopsis to question polling to final result.
- Recover an in-progress quiz session from `sessionStorage` when possible.
- Render result pages from either live store state or backend result retrieval.
- Submit thumbs-up or thumbs-down result feedback with optional comments.
- Apply backend-driven theme, content, and feature flags at runtime.

## Stack

- React 19
- TypeScript
- Vite 7
- React Router DOM 7
- Zustand 5
- Zod
- Tailwind CSS
- Vitest
- Playwright end-to-end and Playwright component testing

## Runtime Architecture

The application root in `src/App.tsx` composes the frontend in this order:

1. `ErrorBoundary` catches render-time failures and falls back to a resettable error page.
2. `ConfigProvider` fetches backend config, validates it, and initializes API timeouts.
3. `ThemeInjector` writes theme colors, fonts, font sizes, and landing-layout tokens into CSS custom properties.
4. `BrowserRouter` and `AppRouter` render route-specific layouts and pages.

### Configuration Bootstrapping

The frontend does not rely exclusively on build-time constants.

- `src/context/ConfigContext.tsx` loads raw config through `src/services/configService.ts`.
- `loadAppConfig()` prefers live backend config in production.
- In local development, failed config fetches can fall back to `src/config/defaultAppConfig.ts`.
- `validateAndNormalizeConfig()` in `src/utils/configValidation.ts` validates the payload and merges it over frontend defaults.
- `initializeApiService()` receives API timeout values from the validated config before business requests begin.

This makes the frontend content, theme, limits, and Turnstile behavior runtime-configurable rather than hardcoded.

### Content Management (Static Pages)

The About, Terms, Privacy, and Donate pages are content-managed via `appconfig.local.yaml` in the backend.
No code changes are needed to update their copy — only the YAML file.

Each page is defined under `quizzical.frontend.content.<pageKey>` with these fields:

| Field | Type | Purpose |
| --- | --- | --- |
| `title` | `string` | The `<h1>` heading shown on the page |
| `description` | `string` (optional) | Short description used for meta/SEO |
| `body` | `string` (optional) | Full page body as a **Markdown** string (preferred) |
| `blocks` | `array` (optional) | Legacy structured blocks (`p`, `h2`, `ul`, `ol`, `markdown`) |

When `body` is present it takes precedence and is rendered via `react-markdown` with GitHub
Flavored Markdown support (tables, strikethrough, task lists). The `@tailwindcss/typography`
plugin provides consistent prose styling via `prose prose-slate dark:prose-invert` classes.

The `markdown` block type within `blocks` allows markdown strings alongside structured blocks
for legacy content or mixed layouts.

### Theme System

Theming is runtime-injected rather than compiled into a static CSS build.

- `ThemeInjector` first applies the checked-in default theme.
- When backend config arrives, the current theme overrides defaults.
- Theme values are transformed into CSS variables such as `--color-*`, `--font-*`, `--font-size-*`, and landing-specific `--lp-*` layout tokens.

## Routing

Routing is defined in `src/router/AppRouter.tsx`.

### Public Routes

| Path | Purpose |
| --- | --- |
| `/` | Landing page and quiz topic entry |
| `/about` | Static about page driven by config content |
| `/terms` | Static terms page driven by config content |
| `/privacy` | Static privacy page driven by config content |
| `/donate` | Donate/support page driven by config content |
| `/result` | Final result page using store or session-derived context |
| `/result/:resultId` | Shareable result route for a persisted session UUID |

### Guarded and Dev Routes

| Path | Purpose |
| --- | --- |
| `/quiz` | Active quiz flow, guarded by the presence of a quiz ID in store state |
| `/dev/result` | Development-only result preview route |

Additional router behavior:

- The app uses a shared layout with `Header`, `Footer`, and route content rendered via `Outlet`.
- `ScrollAndFocusManager` scrolls to top and focuses the main region on navigation.
- `DocumentTitleUpdater` derives titles from the loaded config content.
- Unknown routes fall through to `NotFoundPage`.

## User Flow

### 1. Landing and Quiz Start

The landing page in `src/pages/LandingPage.tsx` is the entry point for quiz creation.

Current behavior:

- Renders subtitle and a `Which [————] am I?` question frame inside `HeroCard`. The configured `landingPage.title` is intentionally not rendered as a visible heading; the question composition is the visual hero. The subtitle is set in **Baloo 2** (rounded display family) at regular weight (400) for a playful tone without heavy emphasis. Subtitle, question frame, and input use fluid `clamp()` typography so the page reads comfortably from 320px phones up to wide desktops; on viewports under 480px the input pill wraps onto its own line below the "Which" word while preserving a 44px touch target. The input pill carries a darker idle border (muted at 0.85 alpha) so the field is immediately discoverable.
- The `HeroCard` surface uses tight padding (`1rem`/`1.5rem`/`2rem` at sm/md/lg breakpoints) and a `64rem` max width so suggestion chips comfortably fit three or more per row on desktop. The card carries a soft layered shadow and a light slate-200 border (no dark/black outline). The page header is intentionally slim and borderless, with minimal vertical gap before the hero card.
- The input placeholder rotates through a curated pool of 1,000+ personality-quiz noun phrases (`src/data/placeholderTopics.ts` + `src/hooks/usePlaceholderRotation.ts`). The first pick on each visit is random, each tick (~2.2s) picks a new entry that is not the immediately previous value, and rotation pauses on focus, while typing, while submitting, and is fully suppressed when the user honours `prefers-reduced-motion: reduce`. The configured `content.landingPage.placeholder` (default `Hogwarts house`) is the calm fallback shown during pause.
- Renders a randomized cloud of personality-quiz suggestion chips beneath the form, each shaped as `Which {noun phrase} am I?`. Suggestions are sampled once per page load from a deduplicated pool of 2,000+ examples built from a freshly curated `src/data/topicExamples.json` catalog (characters, jobs, places, food, animals, arts/media, sports/games, personality frameworks). All chip and placeholder copy uses standardized sentence-case capitalization (uppercase first letter, proper nouns preserved). Chips are intentionally compact with low visual weight (`Which` / `am I?` unbolded, noun phrase medium weight) so more prompts fit per row, and chip text is vertically centered in the pill. The chip cloud scales with viewport width via responsive `:nth-child` rules in `index.css` (≤8 on phones, ≤12 at 480px, ≤24 at 640px, ≤36 at 1024px, ≤48 at 1280px+).
- Clicking a chip populates the input with the bare noun phrase only and re-focuses the field.
- Mounts an invisible Cloudflare Turnstile widget via `src/components/common/Turnstile.tsx`.
- Blocks quiz creation until a valid Turnstile token is available, unless Turnstile is explicitly disabled by config.
- Shows inline loading narration while the quiz start request is in flight.
- Calls `useQuizActions().startQuiz(category, token)` and navigates to `/quiz` on success.

### 2. Quiz Flow

The quiz page in `src/pages/QuizFlowPage.tsx` is a state-driven controller page rather than a simple screen component.

Current behavior:

- Reads current quiz state from `useQuizView()` and `useQuizProgress()`.
- Renders a loading card while the app is idle or polling for the next question.
- Renders `SynopsisView` when the backend has returned the initial synopsis and optional character set.
- Calls `api.proceedQuiz()` when the user advances from synopsis to questions.
- Calls `api.submitAnswer()` and then resumes polling after each answer.
- Redirects to `/result/:quizId` once the store reaches the result state.
- Falls back to `ErrorPage` when the store enters a fatal error state.

### 3. Result Flow

The result page in `src/pages/FinalPage.tsx` supports both warm and cold result loading.

Current behavior:

- Uses the route parameter, current store quiz ID, or session-stored quiz ID to determine the effective result ID.
- Uses in-memory result data immediately if the current quiz is already finished in the store.
- Otherwise fetches the result from the API.
- Renders `ResultProfile` inside `HeroCard`.
- Exposes share-copy behavior using the current browser origin.
- Shows `FeedbackIcons` only when the loaded result belongs to the active local quiz session.

## State Management

Quiz state is managed in `src/store/quizStore.ts` with Zustand and optional devtools integration in development.

### Store Responsibilities

- Persist the active `quizId`.
- Track the current UI view: `idle`, `synopsis`, `question`, `result`, or `error`.
- Track `knownQuestionsCount`, `answeredCount`, polling state, retries, and submission state.
- Hydrate store state from the start response and later poll responses.
- Poll the backend until a new question or final result becomes available.
- Persist a minimal quiz recovery snapshot to `sessionStorage`.
- Attempt session recovery on load when a previous quiz session exists.

### Session Recovery

Session storage helpers live in `src/utils/session.ts`.

Current behavior:

- Stores a quiz ID, quiz snapshot, and timestamp in `sessionStorage`.
- Expires saved quiz state after 1 hour.
- Attempts recovery shortly after app load if a saved quiz ID exists.
- Restores active quizzes by refetching status from the backend, not by trusting stale UI-only data.

## API Integration

The network layer lives in `src/services/apiService.ts`.

### Base URL Resolution

The API base URL is resolved from environment variables using these rules:

1. If `VITE_API_BASE_URL` is an absolute URL, use it directly.
2. Otherwise compose `VITE_API_URL` plus `VITE_API_BASE_URL`.
3. In development, default to `http://localhost:8000/api/v1` when not explicitly configured.

### API Client Behavior

- All requests go through `apiFetch()`.
- Config fetches are allowed before API timeout initialization.
- Request timeouts are enforced through `AbortController` wrapping.
- Abort errors are normalized as benign canceled requests.
- Non-2xx responses are normalized into a consistent `ApiError` shape with `status`, `code`, `errorCode` (stable BE machine code: `RATE_LIMITED`, `SESSION_BUSY`, `PAYLOAD_TOO_LARGE`, `VALIDATION_ERROR`, etc.), `message`, `retriable`, `retryAfterMs` (parsed from `Retry-After` on 429), `traceId` (from response header or envelope body for log correlation), and `details`.
- Components that surface API errors should narrow on `errorCode` rather than parsing free-form `message` strings (see `src/components/result/FeedbackIcons.tsx` for an example mapping `RATE_LIMITED` / `PAYLOAD_TOO_LARGE` / `VALIDATION_ERROR` to user-friendly copy).
- For tests, use `src/test-utils/mockApiError.ts::mockApiError(code, opts?)` to construct realistic `ApiError` objects (real `Error` instance plus `errorCode`, `traceId`, `retriable`, `retryAfterMs`, `status`, `details`) instead of bare `new Error('...')`. This keeps unit and component tests aligned with what `normalizeHttpError()` actually produces in production.
- Request and response payloads are validated with Zod schemas before being used by the UI.

### Current Endpoint Usage

| Function | Backend route | Purpose |
| --- | --- | --- |
| `startQuiz()` | `POST /quiz/start` | Creates a quiz and hydrates synopsis or question state |
| `proceedQuiz()` | `POST /quiz/proceed` | Opens the question-generation gate |
| `getQuizStatus()` | `GET /quiz/status/:quizId` | Fetches the current quiz state snapshot |
| `pollQuizStatus()` | `GET /quiz/status/:quizId` | Polls until a question or result is ready |
| `submitAnswer()` | `POST /quiz/next` | Sends the selected answer for the current question |
| `submitFeedback()` | `POST /feedback` | Submits result sentiment and optional text |
| `getResult()` | `GET /result/:resultId` or status fallback | Retrieves a shareable result |
| `apiFetch('/config')` | `GET /config` | Loads runtime frontend config |

### Result Retrieval Strategy

`getResult()` uses two strategies depending on configuration:

- If `VITE_USE_DB_RESULTS=false`, the frontend prefers live status-derived results.
- If `VITE_USE_DB_RESULTS=true`, it tries the persisted result endpoint first and falls back to live status when the backend returns `404` or `403`.

## Turnstile and Feature Flags

Turnstile behavior is controlled at runtime through `features.turnstile` and `features.turnstileSiteKey`.

Current behavior in `src/components/common/Turnstile.tsx`:

- If the backend disables Turnstile, the component renders nothing and emits a benign bypass token.
- In development, `VITE_TURNSTILE_DEV_MODE=true` short-circuits verification with a fake token.
- Otherwise the component mounts the real Cloudflare Turnstile widget and auto-executes it when configured as invisible.
- The component exposes `window.resetTurnstile()` so the landing page can refresh the token after a backend failure.

## Error Handling

The frontend has multiple layers of error handling:

- `ErrorBoundary` handles uncaught render-time errors.
- `ConfigProvider` shows a blocking configuration load error with retry.
- `QuizFlowPage` uses store-driven error state for recoverable and fatal quiz failures.
- `GlobalErrorDisplay` provides page, banner, and inline error rendering patterns.
- API errors are normalized into a stable shape with `status`, `code`, `message`, and `retriable` flags.

## Local Development

### Prerequisites

- Node.js 20+
- npm
- A running Quizzical backend for live API development, unless using config mocks and Turnstile dev mode

### Install Dependencies

From the `frontend/` directory:

```bash
npm install
```

### Start the Development Server

```bash
npm run dev
```

Additional useful commands:

```bash
npm run dev:result
npm run dev:e2e
npm run build
npm run preview
```

### Environment Variables

The checked-in `.env.example` currently documents these key values:

- `VITE_API_BASE_URL`
- `VITE_API_URL` when composing a base URL from origin plus path
- `VITE_TURNSTILE_SITE_KEY`
- `VITE_TURNSTILE_DEV_MODE`
- `VITE_USE_DB_RESULTS`
- `VITE_USE_MOCK_CONFIG`

## Testing and Quality Checks

The frontend includes unit, integration-style, component, and end-to-end coverage.

### Common Commands

```bash
npm run lint
npm run test:run
npm run test:cov
npm run test-ct
npm run e2e
```

### Current Test Layers

- Vitest covers stores, services, schema validation, configuration behavior, and UI utilities.
- Playwright component testing covers isolated component rendering and interaction paths. The checked-in CT config runs serially (`workers: 1`, `fullyParallel: false`) because local multi-browser parallel startup is flaky in this workspace, especially in Firefox.
- Playwright end-to-end tests exercise routed browser flows. The checked-in Playwright config runs them serially (`workers: 1`) because the local `npm run dev:e2e` Vite server becomes unstable under cross-browser parallel load in this workspace.
- `tests/e2e/scaleHardening.spec.ts` runs against the real Docker backend (defaults to `http://localhost:8000`, override with `E2E_BACKEND_BASE_URL`) and asserts that every API response carries `Server-Timing: app;dur=…` plus an `X-Trace-ID` header, and that `Server-Timing` is exposed via CORS so the browser can read it client-side. The test skips automatically when the backend is unreachable.

## Key Directories

| Path | Purpose |
| --- | --- |
| `src/App.tsx` | Application root and provider composition |
| `src/router/` | Route definitions, layout, guards, and title handling |
| `src/pages/` | Route-level page controllers |
| `src/context/` | Runtime config loading and availability |
| `src/store/` | Zustand quiz state and session recovery logic |
| `src/services/` | API client and config fetch logic |
| `src/components/` | Shared UI, quiz flow, loading, layout, and result components |
| `src/styles/` | Runtime theme injection and related style helpers |
| `src/utils/` | Validation, guards, session persistence, and mapping helpers |
| `tests/` | Fixtures and browser-facing tests |

## Notes for Contributors

- Keep this README aligned with the actual frontend contract. If routing, session behavior, config loading, theme injection, API integration, environment variables, or test commands change, update this file in the same change.
- Prefer documenting current behavior over future plans or design notes.
- Do not turn this file into a scratchpad. Public repo readers should be able to understand how the frontend works and how to run it from this README alone.
```

### 2) Component visual: `LoadingCard`

```ts
import { test, expect } from '@playwright/experimental-ct-react';
import React from 'react';
import { LoadingCard } from '../../src/components/loading/LoadingCard';

test('LoadingCard visual (reduced motion)', async ({ mount, page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const cmp = await mount(<LoadingCard />);
  await expect(cmp).toHaveScreenshot('loading-card.png');
});
```

### 3) Integration on LandingPage

```ts
import { test, expect } from '@playwright/experimental-ct-react';
import React from 'react';
import { MemoryRouter } from 'react-router-dom';
import { LandingPage } from '../../src/pages/LandingPage';
import './fixtures/config';

test('submit → inline narration appears before navigate', async ({ mount, page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await mount(<MemoryRouter><LandingPage /></MemoryRouter>);

  await page.getByLabel(/quiz topic/i).fill('cats');
  await page.getByRole('button', { name: /create|generate/i }).click();
  await page.getByTestId('turnstile').click(); // satisfy mock

  await page.getByRole('button', { name: /create|generate/i }).click();
  await expect(page.getByTestId('lp-loading-inline')).toBeVisible();
  await expect(page.getByTestId('loading-narration')).toContainText(/Thinking/i);
});
```

### 4) Integration on QuizFlowPage (store mocked)

(You already mock `useQuiz*` in CT. Extend the mock to expose a test handle that flips `currentView`.)

```ts
import { test, expect } from '@playwright/experimental-ct-react';
import React from 'react';
import { MemoryRouter } from 'react-router-dom';
import { QuizFlowPage } from '../../src/pages/QuizFlowPage';
import * as store from '../mocks/quizStore.view.mock'; // new: mock that starts in { currentView:'idle', isPolling:true }

test('QuizFlowPage shows narration while polling, then flips to synopsis', async ({ mount, page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await mount(<MemoryRouter><QuizFlowPage /></MemoryRouter>);

  await expect(page.getByTestId('quiz-loading-card')).toBeVisible();
  await expect(page.getByTestId('loading-narration')).toContainText(/Thinking/i);

  // Advance fake time in the browser env (or just wait)
  await page.waitForTimeout(3200);
  await expect(page.getByTestId('loading-narration')).toContainText(/Researching/i);

  // Flip store to synopsis (no API)
  await page.evaluate(() => (window as any).__ct_setQuizView?.({ currentView: 'synopsis', isPolling: false, viewData: { title: 't', summary: 's' } }));
  await expect(page.getByTestId('quiz-loading-card')).toBeHidden();
  await expect(page.getByText(/start|proceed/i)).toBeVisible(); // synopsis view affordance visible
});
```

> If you prefer not to create another mock file, you can extend your existing `quizStore.mock.ts` with a `__ct_setQuizView` bridge similar to your `__ct_setNextStartQuizError`.

---

# Why this works

* **No backend calls in tests.** All timing is driven by the component’s internal clock; store transitions are simulated.
* **No duplication.** One `LoadingCard` used on Landing and Quiz pages yields consistent visuals.
* **Deterministic visuals.** We snapshot with `reducedMotion` so the sprite doesn’t flake the diffs.
* **A11y-safe.** `role="status"` + polite live region; sprite is decorative.
* **Layout safe.** The card shell is identical between states → near-zero CLS; we assert it.

If you want, I can also provide the tiny `quizStore.view.mock.ts` helper used in test #4 and a one-liner analytics hook to log label transitions (`onChangeText`).



