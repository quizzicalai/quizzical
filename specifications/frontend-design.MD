# Quizzical Frontend — Technical Design Specification

> **Source of truth** for all frontend code under `frontend/`. Before changing any file in `frontend/`, update this document with the relevant acceptance criteria and design changes first.

---

## Table of Contents

1. [Overview & Guiding Principles](#1-overview--guiding-principles)
2. [Technology Stack](#2-technology-stack)
3. [Directory Structure](#3-directory-structure)
4. [Startup & Initialization Sequence](#4-startup--initialization-sequence)
5. [Configuration System (Backend-Driven Design)](#5-configuration-system-backend-driven-design)
6. [Routing](#6-routing)
7. [State Management](#7-state-management)
8. [API Integration](#8-api-integration)
9. [Component Architecture](#9-component-architecture)
10. [Types & Schemas](#10-types--schemas)
11. [Styling & Design Token System](#11-styling--design-token-system)
12. [Feature Flags](#12-feature-flags)
13. [Session Recovery](#13-session-recovery)
14. [Accessibility](#14-accessibility)
15. [Testing](#15-testing)
16. [Build & Environment Variables](#16-build--environment-variables)
17. [Magic Numbers Reference](#17-magic-numbers-reference)
18. [Acceptance Criteria & Test Matrix](#18-acceptance-criteria--test-matrix)

---

## 1. Overview & Guiding Principles

Quizzical AI is a single-page React application that lets users generate AI-powered personality quizzes for any topic. The user enters a topic, the backend runs a LangGraph agent to produce a quiz, the user answers questions, and receives a shareable personality result.

### Guiding Principles

- **Backend owns the design.** All visual design tokens (colors, fonts, spacing, layout) are served from the backend via `GET /config` and injected as CSS variables at runtime. The frontend ships with safe defaults but defers to the backend.
- **Type-safe throughout.** Every API boundary is validated with Zod schemas. All components have explicit TypeScript types with no `any` except where documented.
- **Resilient by default.** Network errors fall back to defaults; polling uses exponential backoff; sessions recover from `sessionStorage`; Turnstile bypasses in dev.
- **Accessible first.** Every interactive element has an `aria-label`; loading states use `role=status`; focus is managed on view transitions.
- **Zero layout dependencies.** All styling is via Tailwind + CSS variables. No inline styles except for injected design tokens and dynamic color fallbacks.

---

## 2. Technology Stack

| Layer | Choice | Version |
|-------|--------|---------|
| Framework | React | 19.1.0 |
| Language | TypeScript | strict mode |
| Build | Vite | 7.x |
| Routing | React Router v7 (DOM) | 7.14.2 |
| State | Zustand | 5.0.7 |
| Validation | Zod | 4.x |
| Styling | Tailwind CSS | 3.4.4 |
| CSS post-processing | PostCSS + autoprefixer | — |
| Animations | @uiball/loaders (SuperBalls) | 1.3.1 |
| Unit tests | Vitest + Testing Library | 3.2.4 |
| Component tests | Playwright CT | 1.55.x |
| E2E tests | Playwright | 1.55.x |
| Accessibility | jest-axe + vitest-axe | — |
| HTTP | native `fetch` | — |
| Utility | clsx | 2.1.1 |

---

## 3. Directory Structure

```
frontend/
├── index.html                     # Vite HTML entry; mounts <div id="root">
├── src/
│   ├── main.tsx                   # React entry; mounts App to #root
│   ├── App.tsx                    # Root: ErrorBoundary > ConfigProvider > ThemeInjector > BrowserRouter > AppRouter
│   ├── index.css                  # Tailwind directives + CSS variable defaults
│   │
│   ├── assets/icons/              # SVG icon components (WizardCatIcon, SendIcon, ShareIcon, …)
│   ├── components/
│   │   ├── common/                # Reusable primitives (7 components)
│   │   ├── layout/                # Structural layout (4 components)
│   │   ├── loading/               # Loading states (3 components)
│   │   ├── quiz/                  # Quiz UI (AnswerGrid, AnswerTile, QuestionView, SynopsisView)
│   │   └── result/                # Result + feedback (ResultProfile, FeedbackIcons)
│   │
│   ├── config/
│   │   └── defaultAppConfig.ts    # Full DEFAULT_APP_CONFIG with all fallback values
│   ├── context/
│   │   └── ConfigContext.tsx      # ConfigProvider + useConfig() hook
│   ├── dev/                       # Dev-only result preview (excluded from production routing)
│   ├── hooks/
│   │   ├── useApi.ts              # Generic API hook with deduplication, retries, select
│   │   └── useCloseOnRouteChange.ts
│   ├── pages/                     # 10 page components (1 per route)
│   ├── router/
│   │   └── AppRouter.tsx          # Route definitions, guards, layout wrappers
│   ├── schemas/
│   │   ├── quiz.ts                # Zod schemas for quiz API payloads
│   │   └── status.ts              # Zod schemas for quiz status polling
│   ├── services/
│   │   ├── apiService.ts          # fetch wrapper + all API call functions
│   │   └── configService.ts       # Config loading (HTTP, mock, fallback)
│   ├── store/
│   │   └── quizStore.ts           # Zustand quiz store (state + actions + selectors)
│   ├── styles/
│   │   └── ThemeInjector.tsx      # CSS variable injection from AppConfig.theme
│   ├── types/                     # All TypeScript types (api, config, quiz, result, …)
│   └── utils/
│       ├── color.ts               # toRgbTriplet() — hex/#RGB → "R G B" triplet
│       ├── configValidation.ts    # Zod-based config merge + validateAndNormalizeConfig()
│       ├── quizGuards.ts          # Type guards + API → UI normalizers
│       └── session.ts             # sessionStorage quiz recovery (SessionManager)
│
├── tailwind.config.js             # Tailwind: colors + fonts mapped to CSS vars
├── vite.config.ts                 # Vite: manual chunking (react-vendor, router-vendor, state-vendor)
├── tsconfig.json                  # TypeScript: ES2022, strict, bundler resolution
├── vitest.config.ts               # Vitest: jsdom, coverage v8
├── playwright.config.ts           # Playwright: E2E tests
└── tests/
    ├── vitest.setup.ts            # Global test setup
    ├── ct/                        # Playwright component tests
    ├── e2e/                       # Playwright E2E tests
    └── fixtures/                  # Test fixtures
```

---

## 4. Startup & Initialization Sequence

```
main.tsx
  └── ReactDOM.createRoot(document.getElementById('root'))
       └── <React.StrictMode>
            └── App.tsx
                 ├── ErrorBoundary          — catches all uncaught errors, shows fallback UI
                 └── ConfigProvider         — async: fetches /config, merges with defaults
                      └── ThemeInjector     — injects CSS vars from config.theme to :root
                           └── BrowserRouter
                                └── AppRouter
```

### ConfigProvider Loading Sequence

1. Call `loadAppConfig()` (from `configService.ts`)
2. Based on mode:
   - **E2E** (`VITE_E2E=true`): always HTTP
   - **Dev** (`import.meta.env.DEV`): try HTTP first; fall back to mock if `VITE_USE_MOCK_CONFIG=true` or HTTP fails
   - **Production**: always HTTP; throw on failure
3. Call `validateAndNormalizeConfig(raw)` — deep-merge raw response with `DEFAULT_APP_CONFIG` via Zod schemas
4. Normalize Turnstile flag: `features.turnstile` is authoritative; `turnstileEnabled` is a legacy mirror
5. Call `initializeApiService(config.apiTimeouts)` — configure per-endpoint timeouts
6. Set config state → triggers `ThemeInjector` re-injection

### ThemeInjector Injection Sequence

1. On mount: inject from `DEFAULT_APP_CONFIG.theme` (immediate, before HTTP config arrives)
2. On `config` prop update: re-inject from backend config theme (overrides defaults)

For each theme property:
- Colors → `--color-{key}` (value must be "R G B" triplet)
- Fonts → `--font-body`, `--font-display` (if `sans`/`serif` font keys)
- Font sizes → `--font-size-{key}`
- Landing layout tokens → `--lp-{key}` (e.g., `--lp-cardMaxW`)

Color values are normalized via `toRgbTriplet()`: accepts `#fff`, `#ffffff`, or already-triplet `"R G B"` format.

---

## 5. Configuration System (Backend-Driven Design)

> **Key design decision**: All visual design tokens are *owned by the backend* and served dynamically. The frontend has hard-coded defaults but these are always overridden by the backend's `/config` response.

### Backend Config Source

- **File**: `backend/appconfig.local.yaml` under `quizzical.frontend`
- **Endpoint**: `GET {API_BASE}/config`
- **Cache**: `Cache-Control: public, max-age=60` (default; configurable via `CONFIG_CACHE_SECONDS` env)
- **Fallback**: If endpoint unreachable in dev, `DEFAULT_APP_CONFIG` is used

### Config Shape Served by Backend

```typescript
{
  theme: {
    colors: Record<string, string>,       // RGB triplets e.g. "79 70 229"
    fonts: Record<string, string>,        // e.g. "Inter, sans-serif"
    fontSizes: Record<string, string>,    // e.g. "2.25rem"
    layout: {
      landing: LandingLayoutTokens        // All CSS measurement strings
    }
  },
  content: {
    appName: string,
    landingPage: { title, subtitle, placeholder, buttonText, examples[], inputAriaLabel, validation },
    footer: { about, terms, privacy, donate, copyright },
    aboutPage: { title, description, blocks? },
    termsPage: { title, description, blocks? },
    privacyPolicyPage: { title, description, blocks? },
    resultPage: { titlePrefix?, shareButton?, startOverButton?, feedback?, share? },
    errors: { title, description, retry, home, startOver, categoryNotFound, requestTimeout, resultNotFound },
    loadingStates: { quiz, question, page },
    notFoundPage?: { heading, subheading, buttonText }
  },
  limits: {
    validation: { category_min_length: number, category_max_length: number }
  },
  apiTimeouts: {
    default: number,        // ms
    startQuiz: number,      // ms
    poll: { total, interval, maxInterval }
  },
  features: {
    turnstile: boolean,          // AUTHORITATIVE flag
    turnstileEnabled?: boolean,  // legacy mirror (kept in sync by backend)
    turnstileSiteKey?: string
  }
}
```

### Config Validation

The raw config is validated and merged with `DEFAULT_APP_CONFIG` using Zod strict schemas in `configValidation.ts`:
- Unknown fields are stripped (`.strip()` on all objects)
- Missing required fields fall back to defaults
- `validateAndNormalizeConfig(raw)`: runs parse → on failure, returns defaults + logs warning
- Config is never `null` post-initialization (always has merged defaults)

### CSS Variable Injection Map

| Config key | CSS variable | Example value |
|-----------|--------------|---------------|
| `theme.colors.primary` | `--color-primary` | `0 121 174` |
| `theme.colors.secondary` | `--color-secondary` | `30 41 59` |
| `theme.colors.accent` | `--color-accent` | `234 179 8` |
| `theme.colors.compliment` | `--color-compliment` | `0 121 174` |
| `theme.colors.bg` | `--color-bg` | `238 242 255` |
| `theme.colors.card` | `--color-card` | `255 255 255` |
| `theme.colors.fg` | `--color-fg` | `15 23 42` |
| `theme.colors.border` | `--color-border` | `226 232 240` |
| `theme.colors.muted` | `--color-muted` | `148 163 184` |
| `theme.colors.ring` | `--color-ring` | `129 140 248` |
| `theme.colors.neutral` | `--color-neutral` | `148 163 184` |
| `theme.fonts.sans` | `--font-body` | `Inter, sans-serif` |
| `theme.fonts.serif` | `--font-display` | `Nunito, sans-serif` |
| `theme.fontSizes.body` | `--font-size-body` | `1rem` |
| `theme.fontSizes.landingTitle` | `--font-size-landingTitle` | `2.25rem` |
| `theme.layout.landing.cardMaxW` | `--lp-cardMaxW` | `56rem` |
| `theme.layout.landing.pagePtSm` | `--lp-pagePtSm` | `1.5rem` |
| *(all landing tokens)* | `--lp-{key}` | *(see §11)* |

### Acceptance Criteria

- AC-CFG-1: Frontend loads with no backend available → `DEFAULT_APP_CONFIG` used; app renders
- AC-CFG-2: Backend returns partial config → merged with defaults; no missing-key errors
- AC-CFG-3: Backend returns invalid/extra fields → unknown fields stripped; valid fields used
- AC-CFG-4: Backend config loaded → CSS vars injected on `:root` within 50ms of render
- AC-CFG-5: `features.turnstile = false` in backend config → Turnstile component never renders; bypass token emitted
- AC-CFG-6: `features.turnstileSiteKey` present → used instead of `VITE_TURNSTILE_SITE_KEY`
- AC-CFG-7: Config has `content.landingPage.title` → displayed on landing page (overrides default)
- AC-CFG-8: `Cache-Control: public, max-age=60` header on `/config` → browsers cache response for 60s

---

## 6. Routing

All routes are defined in `src/router/AppRouter.tsx`. Routes are wrapped in `AppLayout` (Header + Outlet + Footer).

### Route Table

| Path | Component | Guard | Layout |
|------|-----------|-------|--------|
| `/` | `LandingPage` | none | AppLayout |
| `/about` | `AboutPage` | none | AppLayout |
| `/terms` | `TermsPage` | none | AppLayout |
| `/privacy` | `PrivacyPage` | none | AppLayout |
| `/quiz` | `QuizFlowPage` | `RequireQuiz` (redirects `/` if no quizId) | AppLayout |
| `/result` | `FinalPage` | none | AppLayout |
| `/result/:resultId` | `FinalPage` | none | AppLayout |
| `/dev/result` | `ResultPreview` | dev-only (not in production) | AppLayout |
| `*` | `NotFoundPage` | none | AppLayout |

### Route Helpers

**`RequireQuiz`**: Checks `useQuizStore(s => s.quizId)`. If `null`, navigates to `/` with `replace`. Wrapped around `/quiz` only.

**`ScrollAndFocusManager`**: On every `location.key` change, scrolls `window` to top and restores focus to the `<main>` element (sets `tabindex="-1"` and calls `.focus()`).

**`DocumentTitleUpdater`**: Sets `document.title` based on current route path. Mapping:
- Base title comes from `config.content.appName` (fallback: `"Quizzical.ai"`)
- `/` → `content.landingPage.title` (fallback base title)
- `/quiz*` → `"Quiz - {baseTitle}"`
- `/result*` → `"Result - {baseTitle}"`
- `/about*` → `content.aboutPage.title` (fallback `"About - {baseTitle}"`)
- `/terms*` → `content.termsPage.title` (fallback `"Terms - {baseTitle}"`)
- `/privacy*` → `content.privacyPolicyPage.title` (fallback `"Privacy - {baseTitle}"`)
- `/dev/result` (dev only) → `"Result Preview - {baseTitle}"`

### Acceptance Criteria

- AC-ROUTE-1: Navigate to `/quiz` with no quizId in store → redirected to `/`
- AC-ROUTE-2: Navigate to `/result/:resultId` → FinalPage loads result from API with that ID
- AC-ROUTE-3: Navigate to `*` (unknown path) → NotFoundPage rendered with 404 content
- AC-ROUTE-4: On any route change → window scrolls to top
- AC-ROUTE-5: `/dev/result` route → only reachable in dev mode (not exposed in production build)

---

## 7. State Management

### 7.1 Zustand Quiz Store (`src/store/quizStore.ts`)

The primary application state. Created with `zustand` + `devtools` middleware.

#### State Shape

```typescript
interface QuizState {
  status: 'idle' | 'loading' | 'active' | 'finished' | 'error';
  currentView: 'idle' | 'synopsis' | 'question' | 'result' | 'error';
  viewData: Synopsis | Question | ResultProfileData | null;
  quizId: string | null;
  knownQuestionsCount: number;  // number of questions fetched so far
  answeredCount: number;        // number of answers submitted
  totalTarget: number;          // expected total questions
  uiError: string | null;
  isSubmittingAnswer: boolean;
  isPolling: boolean;
  retryCount: number;
  lastPersistTime: number;      // timestamp of last sessionStorage write
  sessionRecovered: boolean;    // true if state was loaded from sessionStorage
}
```

#### Actions

| Action | Description |
|--------|-------------|
| `startQuiz(category, turnstileToken)` | Call `POST /quiz/start`; populate quizId + initial view |
| `hydrateFromStart(payload)` | Set view from StartQuizResponse (synopsis or question) |
| `hydrateStatus(dto)` | Update view from status poll result |
| `beginPolling(options)` | Start polling `/quiz/status` with exponential backoff |
| `markAnswered()` | Increment `answeredCount` |
| `submitAnswerStart()` | Set `isSubmittingAnswer = true` |
| `submitAnswerEnd()` | Set `isSubmittingAnswer = false` |
| `setError(message, isFatal)` | Set `uiError`; set status/view to error |
| `clearError()` | Clear `uiError` |
| `recover()` | Transition from error back to previous active view |
| `reset()` | Reset all state to initial values |
| `persistToSession()` | Write snapshot to sessionStorage (rate-limited) |
| `recoverFromSession()` | Load snapshot from sessionStorage if valid |

#### Selectors

```typescript
useQuizView()      // { quizId, currentView, viewData, status, isPolling, isSubmittingAnswer, uiError }
useQuizProgress()  // { answeredCount, totalTarget }
useQuizActions()   // all action functions
```

#### Store Constants

| Constant | Value | Purpose |
|----------|-------|---------|
| `MAX_RETRIES` | 3 | Max polling retry count before error |
| `RETRY_DELAY_MS` | 1500 | Initial retry delay (ms) |
| `MIN_SESSION_PERSIST_INTERVAL_MS` | 1000 | Throttle sessionStorage writes |

### 7.2 ConfigContext (`src/context/ConfigContext.tsx`)

Provides loaded `AppConfig` to the entire component tree.

```typescript
type ConfigContextValue = {
  config: AppConfig | null;
  features: NonNullable<AppConfig['features']>;
  isLoading: boolean;
  error: string | null;
  reload: () => void;
};
```

- `useConfig()`: throws if called outside `ConfigProvider`
- `config` is `null` only during initial load
- `features` always returns a safe default (Turnstile enabled by default until backend says otherwise)

### 7.3 Local Component State

| Component | State |
|-----------|-------|
| `LandingPage` | `category`, `isSubmitting`, `inlineError`, `turnstileToken` |
| `QuizFlowPage` | `submissionError`, `selectedAnswerId` |
| `FinalPage` | `resultData`, `isLoading`, `error` |
| `FeedbackIcons` | `rating`, `comment`, `isSubmitting`, `submitted`, `error`, `turnstileToken` |
| `Footer` | `menuOpen` (mobile nav toggle) |
| `Turnstile` | `widgetRendered`, `retryCount` |

---

## 8. API Integration

### 8.1 Base URL Resolution

Set in `apiService.ts` via `initializeApiService(timeouts)`:
1. If `VITE_API_BASE_URL` starts with `http` → use as full base URL
2. Else if `VITE_API_URL` is set → combine `VITE_API_URL + VITE_API_BASE_URL`
3. Else → use `VITE_API_BASE_URL` as relative path (production default)

Default in `appconfig.local.yaml` dev environment: `http://localhost:8000/api/v1`

### 8.2 Core Fetch Wrapper (`apiFetch`)

```typescript
apiFetch<T>(path: string, options: {
  method?: string,
  body?: unknown,
  signal?: AbortSignal,
  timeout?: number,
  headers?: Record<string, string>
}): Promise<T>
```

- Sets `Content-Type: application/json` on POST/PUT
- Attaches timeout via `AbortController` using `timeoutMs` (`/config` uses 10000ms pre-init)
- Parses JSON response
- Normalizes errors → `ApiError` shape: `{ status, code, message, retriable, details }`
- On `AbortError`: **throws** `{ status: 0, code: 'canceled', canceled: true, retriable: false }` — callers detect via `err.canceled === true` and ignore gracefully
- On 4xx/5xx: reads `payload.code || payload.error` for code; `payload.message || payload.detail` for message; sets `retriable: res.status >= 500` (5xx only — 429 is NOT marked retriable)

### 8.3 All API Endpoints

#### `POST /quiz/start`
- **Function**: `startQuiz(category, turnstileToken, options?)`
- **Body**: `{ category: string, "cf-turnstile-response": string }`
- **Response**: `StartQuizResponse`:
  ```typescript
  {
    quizId: string,
    initialPayload: WrappedQuestion | WrappedSynopsis | null,
    charactersPayload?: WrappedCharacters | null
  }
  ```
- **Timeout**: `apiTimeouts.startQuiz` (default 60000ms)
- **On success**: `quizStore.hydrateFromStart(response)` → navigate to `/quiz`
- **AC-START-1**: Valid category + token → backend returns 201; frontend hydrates store and navigates to `/quiz`
- **AC-START-2**: Category below `category_min_length` → backend returns 422; `inlineError` shown; no navigation (LandingPage has no client-side length validation — only empty-string guard)
- **AC-START-3**: Category above 100 characters → backend returns 422; `inlineError` shown; no navigation (LandingPage has no client-side length validation — only empty-string guard)
- **AC-START-4**: API returns 422/400 → `inlineError` shown on landing page
- **AC-START-5**: Request times out after `startQuiz` timeout → error with `requestTimeout` message

#### `POST /quiz/proceed`
- **Function**: `proceedQuiz(quizId, options?)`
- **Body**: `{ quizId: string }`
- **Response**: `{ status: 'processing', quiz_id|quizId: string }`
- **Timeout**: `apiTimeouts.default` (15000ms)
- **When called**: After `hydrateFromStart` if initial payload was `null`
- **AC-PROCEED-1**: Called with valid quizId → 202; store sets `isPolling = true`

#### `GET /quiz/status/{quizId}`
- **Function**: `getQuizStatus(quizId, options?)`
- **Query param**: `known_questions_count` (optional int)
- **Response**: Discriminated union:
  - `{ status: 'processing' }` → polling continues
  - `{ status: 'active', type: 'question', data: QuestionPayload }` → show question
  - `{ status: 'finished', type: 'result', data: FinalResultApi }` → show result
- **Timeout**: `apiTimeouts.default` (15000ms)
- **AC-STATUS-1**: Returns `processing` → polling continues (no view change)
- **AC-STATUS-2**: Returns active question → `currentView = 'question'`, `viewData = question`
- **AC-STATUS-3**: Returns finished → `currentView = 'result'`; navigate to `/result/{quizId}`

#### `pollQuizStatus(quizId, options?)` (polling wrapper)
- Wraps `getQuizStatus` in a retry loop
- Polls until status is `active` or `finished`
- **Total timeout**: `poll.total` (60000ms)
- **Interval**: starts at `poll.interval` (1000ms), grows with jitter up to `poll.maxInterval` (5000ms)
- **Stops on**: active question, finished result, or total timeout exceeded
- **AC-POLL-1**: Status changes to active within 30s → question displayed within one poll interval
- **AC-POLL-2**: Polling exceeds total timeout → error state with retry option
- **AC-POLL-3**: Polling retry after 3 failures → `MAX_RETRIES` exceeded → fatal error

#### `POST /quiz/next`
- **Function**: `submitAnswer(quizId, { questionIndex, answer?, optionIndex? }, options?)`
- **Body**: `{ quizId, questionIndex, answer?, optionIndex? }`
- **Response**: `{ status: string }`
- **Timeout**: `apiTimeouts.default` (15000ms)
- **On success**: `markAnswered()` → `beginPolling()` for next question
- **AC-NEXT-1**: Submit valid answer → 202; polling starts; answer tile shows spinner
- **AC-NEXT-2**: Both `answer` and `optionIndex` omitted → client guard prevents submission
- **AC-NEXT-3**: API returns 409 → error shown; user can retry or start over

#### `POST /feedback`
- **Function**: `submitFeedback(quizId, { rating, comment }, turnstileToken, options?)`
- **Body**: `{ quiz_id, rating: 'up'|'down', text?, "cf-turnstile-response": token }`
- **Response**: void (204)
- **Timeout**: `apiTimeouts.default` (15000ms)
- **AC-FEEDBACK-1**: Submit thumbs up/down → 204; "Thank you" message shown
- **AC-FEEDBACK-2**: Submit without Turnstile token (when required) → error shown; no submission

#### `GET /result/{resultId}`
- **Function**: `getResult(resultId, options?)`
- **Response**: `ResultProfileData`
- **Timeout**: `apiTimeouts.default` (15000ms)
- **When called**: On `FinalPage` mount if no result in store (cold load from URL)
- **AC-RESULT-1**: Valid resultId → result displayed
- **AC-RESULT-2**: Invalid/expired resultId → 404 error; `resultNotFound` message shown

#### `GET /config`
- **Function**: `fetchBackendConfig(options?)`
- **Timeout**: 10000ms (hardcoded pre-initialization)
- **Response**: Partial `AppConfig` (missing fields fall back to defaults)
- **AC-CONFIG-1**: Config loaded → CSS vars injected; all content updated
- **AC-CONFIG-2**: Config fetch fails in dev → mock config used; no error displayed to user

### 8.4 Error Normalization

All API errors are normalized to `ApiError`:
```typescript
type ApiError = {
  status?: number;
  code?: string;
  message?: string;
  retriable?: boolean;
  details?: unknown;
  canceled?: boolean;
};
```

`retriable = true` when: `status >= 500` (5xx responses only; 429 is not marked retriable)

---

## 9. Component Architecture

### 9.1 App Shell Components

#### `App` (`src/App.tsx`)
Entry point wrapper. Composes:
```
ErrorBoundary > ConfigProvider > ThemeInjector > BrowserRouter > AppRouter
```

#### `AppLayout` (in `AppRouter.tsx`)
- Props: none (uses `<Outlet />`)
- Structure: `<div className="flex flex-col min-h-screen"> <Header /> <main> <Outlet /> </main> <Footer /> </div>`

#### `ThemeInjector` (`src/styles/ThemeInjector.tsx`)
- Props: `config: AppConfig | null`
- Injects CSS variables to `document.documentElement.style`
- Phase 1: inject from `DEFAULT_APP_CONFIG.theme` on mount
- Phase 2: inject from `config.theme` when config prop changes
- Pure side-effect component; renders nothing visible

### 9.2 Common Components

#### `ErrorBoundary` (`src/components/common/ErrorBoundary.tsx`)
- Class component (required for `componentDidCatch`)
- Props: `children`, `fallback?: ReactNode`
- On error: renders fallback or default error UI with reload button
- Dev mode: shows error stack in `<details>` block

#### `GlobalErrorDisplay` (`src/components/common/GlobalErrorDisplay.tsx`)
- Props: `variant: 'inline' | 'page' | 'banner'`, `error: ApiError`, `labels?`, `onRetry?`, `onStartOver?`, `icon?`
- `inline`: card within content flow; auto-focuses on mount
- `page`: full-page centered error
- `banner`: top-of-page dismissible banner
- Shows "Try Again" if `error.retriable`; else "Start Over"
- ARIA: `role="alert"`, `aria-live="assertive"`

#### `Spinner` (`src/components/common/Spinner.tsx`)
- Props: `size: 'sm' | 'md' | 'lg'`, `message?: string`, `className?: string`
- Sizes: `sm` = w-4 h-4, `md` = w-8 h-8, `lg` = w-12 h-12
- Color: `border-primary` (Tailwind; uses `--color-primary` CSS var)
- Animation: CSS `animate-spin`

#### `Turnstile` (`src/components/common/Turnstile.tsx`)
- Props: `onVerify(token)`, `onError?`, `onExpire?`, `theme?`, `size?`, `autoExecute?: boolean`
- Bypass conditions (in order):
  1. If `features.turnstile === false` → emit bypass token `bypass-{timestamp}` immediately
  2. If `VITE_TURNSTILE_DEV_MODE=true` and Turnstile is enabled → emit dev token after 50ms
  3. Else: render real Cloudflare Turnstile widget
- Auto-execute: invisible widgets re-execute on expiry
- Site key: from `features.turnstileSiteKey` → fallback `VITE_TURNSTILE_SITE_KEY` (no built-in test-key fallback in component)
- Retry: up to 10 render attempts on widget/script readiness failure (300ms delay)

#### `InlineError` (`src/components/common/InlineError.tsx`)
- Props: `message: string`, `onRetry?: () => void`
- Red alert box with message + optional "Try Again" button
- ARIA: `role="alert"`

#### `IconButton` (`src/components/common/IconButton.tsx`)
- Props: `Icon`, `label`, `onClick?`, `disabled?`, `size: 'sm'|'md'|'lg'`, `variant: 'primary'|'neutral'|'danger'`
- Size → button diameter: `sm`=40px, `md`=44px, `lg`=48px
- Icon size: `sm`=16px, `md`=20px, `lg`=24px
- Styling: CSS vars for colors; hover: lift + shadow; active: scale down; disabled: opacity 40%

#### `Image` (`src/components/common/Image.tsx`)
- Props: `src`, `alt`, `className?`
- On load error: replaces `src` with `https://placehold.co/600x400/e2e8f0/475569?text=Image+Not+Found`
- Fade-in animation on successful load

#### `InputGroup` (`src/components/common/InputGroup.tsx`)
> **Note**: This component is NOT used by `LandingPage` directly. `LandingPage` builds its own form with a plain `<input>` + `IconButton` (using `ArrowIcon`). `InputGroup` is available for other use cases.
- Props: `value`, `onChange`, `placeholder?`, `errorText?`, `minLength?`, `maxLength?`, `isSubmitting?`, `ariaLabel`, `buttonText`, `validationMessages?`
- Client-side validation messages: configurable via `validationMessages` (defaults from `content.landingPage.validation`)
- Layout: pill-shaped input + submit button in a flex row
- Submit button: `SendIcon` on right; circle shape; size from `--lp-submitSize`
- Error display: `InlineError` below input

### 9.3 Layout Components

#### `Header` (`src/components/layout/Header.tsx`)
- Logo: `WizardCatIcon` SVG + app name text from `content.appName`
- Logo is a `<button>` that navigates to `/`
- ARIA: `aria-label="Go to home page"`

#### `Footer` (`src/components/layout/Footer.tsx`)
- Props: `variant: 'landing' | 'quiz'` (visual adjustments)
- Links: About, Terms, Privacy, Donate (from `content.footer`)
- Copyright: `content.footer.copyright`
- Mobile: hamburger toggle; ARIA: `aria-expanded`, `aria-controls="footer-nav"`, `aria-haspopup="true"`
- External links: `target="_blank" rel="noopener noreferrer"`

#### `HeroCard` (`src/components/layout/HeroCard.tsx`)
- Props: `children`, `className?`, `contentClassName?`, `ariaLabel?`, `showHero?: boolean`
- Wrapper card with optional hero icon (WizardCatIcon) and color blob
- Card dimensions from CSS vars: `--lp-cardMaxW`, `--lp-cardPad*`, `--lp-cardRadius`, `--lp-cardShadow`
- Hero icon height from `--lp-heroH*`
- Blob behind hero: size from `--lp-blobSize*`, opacity from `--lp-blobOpacity`

### 9.4 Loading Components

#### `LoadingCard` (`src/components/loading/LoadingCard.tsx`)
- Renders `HeroCard` containing `WhimsySprite` + `LoadingNarration`
- Used during quiz startup and polling states

#### `WhimsySprite` (`src/components/loading/WhimsySprite.tsx`)
- `SuperBalls` loader from `@uiball/loaders`
- Color: reads computed `--color-primary` via `getComputedStyle(document.documentElement)`
- Freeze support: pauses if `window.__FREEZE_LOADERS__ === true` or `data-freeze-loaders` attribute on document (used in tests)

#### `LoadingNarration` (`src/components/loading/LoadingNarration.tsx`)
- Props: `lines?: {atMs: number, text: string}[]`, `onChangeText?`, `tickMs?: number`, `ariaLabel?`
- Cycles through text lines at their `atMs` timestamps
- Default (startup) lines: `Thinking…`, `Researching topic…`, `Determining personality types…`, `Writing profiles…`, `Preparing topic…`
- In-quiz lines (`QUIZ_PROGRESS_LINES`): `Thinking…`, `Planning quiz…`, `Generating questions…`, `Refining options…`
- ARIA: `role="status"`, `aria-live="polite"`

### 9.5 Quiz Components

#### `SynopsisView` (`src/components/quiz/SynopsisView.tsx`)
- Props: `synopsis: Synopsis`, `characters?: Character[]`, `onProceed()`, `onStartOver?()`, `isLoading`, `inlineError?`, `onRetry?`
- Shows title (h1), optional hero image, summary text, "Start Quiz" button
- Characters section: grid (1 col mobile, 2 cols md+) with name, short description, profile text per card
- Characters have optional `imageUrl` (fallback: `Image` component handles broken links)

### 9.5A Landing Topic Explorer Components

#### `TopicSuggestionExplorer` (`src/components/landing/TopicSuggestionExplorer.tsx`)
- Purpose: render a randomized, highly diverse set of clickable topic chips beneath the landing input.
- Data source: JSON-backed topic catalog in `src/data/topicExamples.json`.
- Core behavior:
  - Selects a diverse random subset on mount.
  - Supports reshuffle via a secondary action button.
  - Clicking a chip populates landing input with that topic.
- Accessibility:
  - Section heading is announced to screen readers.
  - Each chip is a semantic `<button>` with clear accessible name.
  - Reshuffle button exposes explicit label (`Shuffle ideas`).
- Responsive pattern:
  - Mobile: wrapped two-row dense chip layout, touch-friendly targets.
  - Desktop: wider wrapped cloud with richer scanability.

#### `topicSuggestions` utility (`src/utils/topicSuggestions.ts`)
- Purpose: select unique, diverse, random suggestions from the catalog.
- Diversity rule: prioritize broad family coverage first, then fill remaining slots randomly.
- Deterministic testability: utility accepts injectable random function for unit tests.

#### `QuestionView` (`src/components/quiz/QuestionView.tsx`)
- Props: `question: Question`, `onSelectAnswer(answerId)`, `isLoading`, `inlineError?`, `onRetry?`, `progress: {answered, total}`, `selectedAnswerId?`
- Question text in `<h2>` (auto-focused on new question)
- Progress: `"Question {answered+1} of {total}"` above the question
- `AnswerGrid` below question

#### `AnswerGrid` (`src/components/quiz/AnswerGrid.tsx`)
- Props: `answers: Answer[]`, `disabled?`, `onSelect(id)`, `selectedId?`
- Layout: 1-col mobile; 2-col `sm:` desktop; `gap-4`
- Odd last answer: spans 2 cols centered on desktop (`col-span-2 mx-auto max-w-xs`)
- Each tile: `AnswerTile` (memoized with `React.memo`)

#### `AnswerTile` (`src/components/quiz/AnswerTile.tsx`)
- Props: `answer: Answer`, `disabled`, `isSelected`, `onClick`
- Optional image + text
- States: hover (translateY -2px + shadow), active (scale 0.98), selected (ring + color overlay), disabled (opacity 60%)
- Shows `Spinner sm` when selected and `disabled` (submission in progress)

### 9.6 Result Components

#### `ResultProfile` (`src/components/result/ResultProfile.tsx`)
- Props: `result: ResultProfileData`, `labels?`, `shareUrl?`, `onCopyShare?()`, `onStartNew?()`
- Title (h1) auto-focused on mount
- Optional image (full-width rounded)
- Summary text
- Traits list: `traitListTitle` label + `{label}: {value}` per trait
- Share button: native Web Share API → fallback clipboard copy
- Share copy text: `content.resultPage.shareText`

#### `FeedbackIcons` (`src/components/result/FeedbackIcons.tsx`)
- Props: `quizId: string`, `labels?`
- Flow: thumbs up (😄) / thumbs down (😕) → optional comment textarea → Turnstile verification → submit
- Submit: `submitFeedback(quizId, { rating, comment }, token)`
- On success: shows `content.resultPage.feedback.thanks`

---

## 10. Types & Schemas

### 10.1 API Types (`src/types/api.ts`)

```typescript
type ApiError = {
  status?: number;
  code?: string;
  message?: string;
  retriable?: boolean;
  details?: unknown;
  canceled?: boolean;
};
```

### 10.2 Quiz Types (`src/types/quiz.ts`)

```typescript
type Answer = { id: string; text: string; imageUrl?: string; imageAlt?: string };
type Question = { id: string; text: string; imageUrl?: string; imageAlt?: string; answers: Answer[] };
type Character = { name: string; shortDescription: string; profileText: string; imageUrl?: string };
type Synopsis = { id?: string; title: string; imageUrl?: string; imageAlt?: string; summary: string; characters?: Character[] };
```

### 10.3 Result Types (`src/types/result.ts`)

```typescript
type Trait = { id?: string; label: string; value?: string };
type ResultProfileData = { id?: string; profileTitle: string; imageUrl?: string; imageAlt?: string; summary: string; traits?: Trait[]; shareUrl?: string };
type FinalResultApi = { title: string; imageUrl: string | null; description: string };
```

### 10.4 Config Types (`src/types/config.ts`)
Full type hierarchy: `AppConfig → ThemeConfig + ContentConfig + limits + apiTimeouts + features`
See §5 for full shape.

### 10.5 Zod Schemas

#### `src/schemas/quiz.ts`
| Schema | Validates |
|--------|-----------|
| `AnswerOptionSchema` | `{ text, imageUrl? }` |
| `QuestionSchema` | `{ text, imageUrl?, options[] }` |
| `SynopsisSchema` | `{ type: 'synopsis', title, summary }` |
| `CharacterProfileSchema` | `{ name, shortDescription, profileText, imageUrl? }` |
| `FrontendStartQuizResponseSchema` | `{ quizId, initialPayload, charactersPayload? }` |
| `TraitSchema` | `{ id?, label, value? }` |
| `ShareableResultSchema` | `{ title, description, imageUrl?, traits?, shareUrl? }` |
| `FinalResultSchema` | Full result shape with optional fields |

#### `src/schemas/status.ts`
| Schema | Validates |
|--------|-----------|
| `ProcessingResponseSchema` | `{ status: 'processing', quizId? }` |
| `QuizStatusQuestionSchema` | `{ status: 'active', type: 'question', data }` |
| `QuizStatusResultSchema` | `{ status: 'finished', type: 'result', data }` |
| `QuizStatusResponseSchema` | Union of above 3 |

### 10.6 API → UI Normalizers (`src/utils/quizGuards.ts`)

| Guard/Normalizer | Input | Output |
|-----------------|-------|--------|
| `isWrappedSynopsis(p)` | unknown | bool |
| `isWrappedQuestion(p)` | unknown | bool |
| `isWrappedCharacters(p)` | unknown | bool |
| `isRawQuestion(p)` | unknown | bool (has `answers[]`) |
| `isRawSynopsis(p)` | unknown | bool (has `title` + `summary`) |
| `toUiAnswers(options)` | Backend options (string/dict/mixed) | `Answer[]` |
| `toUiQuestionFromApi(raw)` | Backend question | `Question` (options → answers) |
| `toUiCharacters(raw)` | Snake_case characters | camelCase `Character[]` |
| `toUiResult(raw)` | Backend result | `ResultProfileData` |

Normalizers tolerate legacy fields: snake_case aliases, partial shapes, null fields.

---

## 11. Styling & Design Token System

### 11.1 Architecture

- **Framework**: Tailwind CSS 3.4.4 (utility-first)
- **Color system**: CSS variables using RGB triplets (`--color-*`)
- **Typography**: CSS variables (`--font-body`, `--font-display`, `--font-size-*`)
- **Layout**: CSS variables (`--lp-*` for landing page tokens)
- **Dark mode**: class-based (`dark:` prefix); not currently applied by default
- **No inline styles** except for injected CSS vars and dynamic runtime fallback values

### 11.2 Tailwind Config

Colors, text colors, and font families are all mapped to CSS variables:
```javascript
colors: { primary: withOpacity('--color-primary'), ... }
fontFamily: { sans: ['var(--font-body)', ...], display: ['var(--font-display)', ...] }
```

### 11.3 CSS Variable Defaults (`src/index.css`)

Applied at `:root` before `ThemeInjector` runs. Overridden by ThemeInjector on mount.

```css
--color-primary:    79 70 229   /* indigo-600 */
--color-secondary:  30 41 59    /* slate-800 */
--color-compliment: 0 121 174   /* cyan-700 */
--color-accent:     234 179 8   /* amber-400 */
--color-bg:         238 242 255 /* indigo-50 */
--color-card:       255 255 255 /* white */
--color-fg:         15 23 42    /* slate-900 */
--color-border:     226 232 240 /* slate-200 */
--color-muted:      148 163 184 /* slate-400 */
--color-ring:       129 140 248 /* indigo-400 */
--color-neutral:    148 163 184 /* slate-400 */
```

### 11.4 Landing Layout Token Map (`--lp-*`)

All values are CSS strings (include units). Sourced from `theme.layout.landing` in backend config.

| Token | CSS Var | Default |
|-------|---------|---------|
| `pagePtSm` | `--lp-pagePtSm` | `1.5rem` |
| `pagePtMd` | `--lp-pagePtMd` | `3rem` |
| `pagePtLg` | `--lp-pagePtLg` | `4rem` |
| `cardMaxW` | `--lp-cardMaxW` | `56rem` |
| `cardPadSm` | `--lp-cardPadSm` | `2rem` |
| `cardPadMd` | `--lp-cardPadMd` | `3rem` |
| `cardPadLg` | `--lp-cardPadLg` | `4rem` |
| `cardRadius` | `--lp-cardRadius` | `1rem` |
| `cardShadow` | `--lp-cardShadow` | box-shadow with 2 layers |
| `heroHSm` | `--lp-heroHSm` | `6rem` |
| `heroHMd` | `--lp-heroHMd` | `7rem` |
| `heroHLg` | `--lp-heroHLg` | `8rem` |
| `blobSizeSm` | `--lp-blobSizeSm` | `6rem` |
| `blobSizeMd` | `--lp-blobSizeMd` | `7rem` |
| `blobSizeLg` | `--lp-blobSizeLg` | `8rem` |
| `blobOpacity` | `--lp-blobOpacity` | `0.18` |
| `spaceAfterHeroSm` | `--lp-spaceAfterHeroSm` | `1.5rem` |
| `spaceAfterHeroMd` | `--lp-spaceAfterHeroMd` | `2rem` |
| `titleMaxW` | `--lp-titleMaxW` | `42rem` |
| `subtitleMaxW` | `--lp-subtitleMaxW` | `38rem` |
| `spaceTitleToSubtitleSm` | `--lp-spaceTitleToSubtitleSm` | `1rem` |
| `spaceTitleToSubtitleMd` | `--lp-spaceTitleToSubtitleMd` | `1.25rem` |
| `spaceSubtitleToFormSm` | `--lp-spaceSubtitleToFormSm` | `2.5rem` |
| `spaceSubtitleToFormMd` | `--lp-spaceSubtitleToFormMd` | `3rem` |
| `formMaxW` | `--lp-formMaxW` | `36rem` |
| `inputHeight` | `--lp-inputHeight` | `2.5rem` |
| `pillGap` | `--lp-pillGap` | `0.75rem` |
| `pillPl` | `--lp-pillPl` | `1.25rem` |
| `pillPad` | `--lp-pillPad` | `0.375rem` |
| `pillBorder` | `--lp-pillBorder` | `1px solid rgba(var(--color-muted), 0.55)` |
| `pillBg` | `--lp-pillBg` | `rgba(var(--color-card), 0.9)` |
| `ringAlpha` | `--lp-ringAlpha` | `0.2` |
| `submitSize` | `--lp-submitSize` | `2.5rem` |
| `underlineWidth` | `--lp-underlineWidth` | `8rem` |
| `underlineHeight` | `--lp-underlineHeight` | `6px` |
| `underlineRadius` | `--lp-underlineRadius` | `9999px` |

---

## 12. Feature Flags

### Turnstile (CAPTCHA)

- **Backend-authoritative**: `features.turnstile` from `/config` response
- **Default**: `true` (enabled by default until backend explicitly disables)
- **Dev bypass**: `VITE_TURNSTILE_DEV_MODE=true` (explicit opt-in) → immediate token after 50ms
- **Site key priority**: `features.turnstileSiteKey` → `VITE_TURNSTILE_SITE_KEY`
- **Used on**: `LandingPage` (invisible, auto-execute), `FeedbackIcons` (explicit verification before submit)

---

## 13. Session Recovery

Session state is persisted to `sessionStorage` to survive page reloads.

### Keys

| Key | Content |
|-----|---------|
| `quizzical_quiz_id` | Current `quizId` string |
| `quizzical_quiz_state` | `QuizStateSnapshot` JSON |
| `quizzical_quiz_timestamp` | Unix-epoch milliseconds string from `Date.now().toString()` |

### `QuizStateSnapshot`
```typescript
interface QuizStateSnapshot {
  quizId: string;
  currentView: string;
  answeredCount: number;
  knownQuestionsCount: number;
  timestamp?: number;
}
```

### Session Timeout
- **Duration**: 3600000ms (1 hour) — matches backend Redis quiz session TTL
- `isSessionExpired()`: checks `Date.now() - timestamp > SESSION_TIMEOUT_MS`
- Expired sessions are cleared on next `getQuizId()` call

### Recovery Flow
1. On `QuizFlowPage` mount with `status = 'idle'`
2. `recoverFromSession()` called
3. If valid quizId found + not expired → `status` transitions; store populated
4. `beginPolling()` called to resume from current state
5. `sessionRecovered = true` set in store

---

## 14. Accessibility

- All interactive elements (`<button>`, `<a>`) have accessible names via `aria-label` or visible text
- Focus managed on view transitions:
  - `QuestionView`: h2 auto-focused on new question
  - `ResultProfile`: h1 auto-focused on result display
  - `GlobalErrorDisplay`: first focusable element receives focus on mount
- Loading states: `role="status"`, `aria-live="polite"` (`LoadingNarration`)
- Error alerts: `role="alert"`, `aria-live="assertive"` (`InlineError`, `GlobalErrorDisplay`)
- Footer nav: `aria-expanded`, `aria-controls`, `aria-haspopup` on hamburger
- Skip navigation: Header provides landmark regions (`<header>`, `<main>`, `<footer>`)
- Images: always have descriptive `alt` text; fallback images have descriptive alt
- Color contrast: primary color on white background must meet WCAG AA (4.5:1)
- Smoke tests: `src/__a11y__/a11y.smoke.spec.tsx` runs axe on critical pages

---

## 15. Testing

### 15.1 Unit Tests (Vitest + Testing Library)

- **Runner**: Vitest 3.x, jsdom environment
- **Setup**: `tests/vitest.setup.ts` (jest-dom matchers)
- **Pattern**: `src/**/*.{spec,test}.ts(x)` + `tests/**/*.spec.ts(x)`
- **Exclude**: `tests/e2e/**`, `tests/ct/**`

| File | Tests |
|------|-------|
| `ErrorBoundary.spec.tsx` | Error catching, fallback UI, dev stack trace |
| `Spinner.spec.tsx` | Size variants, message display |
| `Turnstile.spec.tsx` | Dev bypass, disabled bypass, real widget render |
| `InputGroup.spec.tsx` | Validation messages, error display, submit |
| `QuestionView.spec.tsx` | Question rendering, focus management |
| `AnswerGrid.spec.tsx` | Grid layout, odd-last centering |
| `AnswerTile.spec.tsx` | State transitions, spinner on selected |
| `ResultProfile.spec.tsx` | Result display, share button, traits |
| `FeedbackIcons.spec.tsx` | Rating flow, comment, Turnstile |
| `LandingPage.spec.tsx` | Form submission, validation, Turnstile flow |
| `QuizFlowPage.spec.tsx` | View transitions, polling, session recovery |
| `FinalPage.spec.tsx` | Result fetch, cold load, error states |
| `AppRouter.spec.tsx` | Routes, lazy loading, RequireQuiz guard |
| `ConfigContext.spec.tsx` | Config load, fallback, merge, Turnstile flag |
| `quizStore.spec.ts` | All state mutations, polling, retries, recovery |
| `useApi.spec.ts` | Hook lifecycle, deduping, retries, callbacks |
| `quizGuards.spec.ts` | All type guards, normalizer tolerances |
| `session.spec.ts` | Storage, expiry, snapshot round-trip |
| `configValidation.spec.ts` | Zod schemas, merge, unknown field stripping |
| `color.spec.ts` | Hex/triplet conversion, null returns |
| `ThemeInjector.spec.tsx` | CSS var injection, two-phase injection |

### 15.2 Component Tests (Playwright CT)

Located in `tests/ct/`. Render components in isolation in a real browser. Used for:
- Visual regression of rendered components
- Complex interaction testing with real DOM

### 15.3 E2E Tests (Playwright)

- **Location**: `tests/e2e/`
- **Browsers**: Chromium, Firefox, WebKit
- **Target**: Runs against dev server (`http://localhost:5173`) or Docker stack

Key test files:
| File | Scope |
|------|-------|
| `account-lifecycle.spec.ts` | Sign up → quiz → result full flow |
| `auth-session.spec.ts` | Login, logout, session handling |
| `chapter-advancement.spec.ts` | Multi-question flow |
| `error-resilience.spec.ts` | Error states, retry, start over |
| `feedback-submission.spec.ts` | Rating + comment flow |
| `landing.spec.ts` | Landing page validation, topic submission |
| `live-api-contract.spec.ts` | Contract validation against real API |

### 15.4 Accessibility Smoke Test

- `src/__a11y__/a11y.smoke.spec.tsx`
- Renders key pages with mock config
- Runs `jest-axe` / `vitest-axe` on each
- Must have zero axe violations

---

## 16. Build & Environment Variables

### Environment Files

| File | Usage |
|------|-------|
| `.env` | Local dev defaults |
| `.env.e2e` | E2E test overrides |
| `.env.example` | Documentation template |

### All `import.meta.env` Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `VITE_API_BASE_URL` | Yes | API base URL (absolute or relative path) |
| `VITE_API_URL` | No | API origin if `VITE_API_BASE_URL` is a path |
| `VITE_TURNSTILE_SITE_KEY` | No | Turnstile site key (overridden by backend config) |
| `VITE_TURNSTILE_DEV_MODE` | No | `'true'` to bypass Turnstile in dev |
| `VITE_USE_MOCK_CONFIG` | No | `'true'` to use mock config (dev only) |
| `VITE_E2E` | No | `'true'` in E2E mode (forces HTTP config load) |
| `VITE_USE_DB_RESULTS` | No | `'true'` to use DB-backed results |
| `import.meta.env.DEV` | — | Vite built-in dev flag |
| `import.meta.env.MODE` | — | Vite mode string |

### Vite Build Config

- **Manual chunks**: `react-vendor` (React + ReactDOM), `router-vendor` (React Router), `state-vendor` (Zod + Zustand)
- **Chunk size warning**: 500 KB
- **Source maps**: disabled in production
- **Output**: `dist/`

### Docker

- `Dockerfile`: multi-stage (node build → nginx:alpine)
- `nginx.conf`: reverse proxy + SPA fallback (`try_files $uri /index.html`)
- `staticwebapp.config.json`: Azure Static Web App routing rules

---

## 17. Magic Numbers Reference

| Constant | Value | Location | Configurable |
|----------|-------|----------|--------------|
| `MAX_RETRIES` | 3 | `quizStore.ts` | No |
| `RETRY_DELAY_MS` | 1500ms | `quizStore.ts` | No |
| `MIN_SESSION_PERSIST_INTERVAL_MS` | 1000ms | `quizStore.ts` | No |
| `SESSION_TIMEOUT_MS` | 3600000ms (1h) | `session.ts` | No |
| `apiTimeouts.default` | 15000ms | `defaultAppConfig.ts` | Via backend config |
| `apiTimeouts.startQuiz` | 60000ms | `defaultAppConfig.ts` | Via backend config |
| `poll.total` | 60000ms | `defaultAppConfig.ts` | Via backend config |
| `poll.interval` | 1000ms | `defaultAppConfig.ts` | Via backend config |
| `poll.maxInterval` | 5000ms | `defaultAppConfig.ts` | Via backend config |
| `CONFIG_FETCH_TIMEOUT` | 10000ms | `configService.ts` | No |
| `category_min_length` | 3 | `defaultAppConfig.ts` | Via backend config |
| `category_max_length` | 80 | `defaultAppConfig.ts` | Via backend config |
| `IconButton sm` size | 40px | `IconButton.tsx` | No |
| `IconButton md` size | 44px | `IconButton.tsx` | No |
| `IconButton lg` size | 48px | `IconButton.tsx` | No |
| Turnstile dev delay | 50ms | `Turnstile.tsx` | No |
| Turnstile max retries | 10 | `Turnstile.tsx` | No |
| Image fallback URL | `https://placehold.co/…` | `Image.tsx` | No |
| Session storage key prefix | `quizzical_` | `session.ts` | No |

---

## 18. Acceptance Criteria & Test Matrix

### Functional

| AC | Condition | Expected |
|----|-----------|----------|
| AC-FE-INIT-1 | App mounts with no backend | Renders with default config; no crash |
| AC-FE-INIT-2 | Backend config loads | CSS vars injected; content updated |
| AC-FE-INIT-3 | Config endpoint errors (prod) | App shows error; no partial render |
| AC-FE-LAND-1 | Topic input < min_length | Start request is sent; backend returns validation error; inline error shown |
| AC-FE-LAND-2 | Topic input > 100 characters | Start request is sent; backend returns validation error; inline error shown |
| AC-FE-LAND-3 | Valid topic submitted | Navigates to `/quiz`; LoadingCard shown |
| AC-FE-LAND-4 | Landing page loads | Diverse randomized topic chips are visible and clickable |
| AC-FE-LAND-5 | User clicks a suggested topic chip | Topic input is populated with the chip text |
| AC-FE-LAND-6 | User clicks shuffle ideas | A new randomized set is shown while preserving diversity |
| AC-FE-QUIZ-1 | Synopsis received | SynopsisView rendered with title + summary |
| AC-FE-QUIZ-2 | Characters received | Character cards shown in synopsis |
| AC-FE-QUIZ-3 | Question received | QuestionView with h2 + AnswerGrid |
| AC-FE-QUIZ-4 | Answer selected | Spinner shown on tile; polling starts |
| AC-FE-QUIZ-5 | Polling returns new question | QuestionView updates; h2 auto-focused |
| AC-FE-QUIZ-6 | All questions answered | `/result/{quizId}` navigated to |
| AC-FE-RESULT-1 | ResultProfile shown | h1 with profile title auto-focused |
| AC-FE-RESULT-2 | Share button clicked | Native share → fallback clipboard copy |
| AC-FE-RESULT-3 | Cold load `/result/:id` | API fetch → result displayed |
| AC-FE-SESSION-1 | Page reload mid-quiz | State recovered from sessionStorage |
| AC-FE-SESSION-2 | Session > 1 hour old | Session discarded; redirect to home |

### Error Handling

| AC | Condition | Expected |
|----|-----------|----------|
| AC-FE-ERR-1 | Quiz start times out | `requestTimeout` error message shown |
| AC-FE-ERR-2 | Polling times out | Error with retry option |
| AC-FE-ERR-3 | Answer submission fails | `submissionError` inline; retry available |
| AC-FE-ERR-4 | Result not found (404) | `resultNotFound` message; no crash |
| AC-FE-ERR-5 | Unknown route | NotFoundPage with heading + home link |
| AC-FE-ERR-6 | Uncaught exception | ErrorBoundary fallback with reload button |

### Security

| AC | Condition | Expected |
|----|-----------|----------|
| AC-FE-SEC-1 | Turnstile required; no token | Submission blocked; error shown |
| AC-FE-SEC-2 | External footer link | `target="_blank" rel="noopener noreferrer"` |
| AC-FE-SEC-3 | Image URL from API | Never used as `innerHTML`; only in `src` attr |
| AC-FE-SEC-4 | API error detail | Never rendered as raw HTML; always escaped |

### Accessibility

| AC | Condition | Expected |
|----|-----------|----------|
| AC-FE-A11Y-1 | axe scan on landing page | Zero violations |
| AC-FE-A11Y-2 | axe scan on quiz flow | Zero violations |
| AC-FE-A11Y-3 | axe scan on result page | Zero violations |
| AC-FE-A11Y-4 | New question shown | h2 auto-focused |
| AC-FE-A11Y-5 | Loading state active | `role=status`, `aria-live=polite` present |

### Test File Map

| Category | File(s) | AC coverage |
|----------|---------|-------------|
| Unit | `src/**/*.spec.tsx` | FE-INIT, FE-LAND, FE-QUIZ, FE-RESULT, FE-SESSION, FE-ERR |
| Integration | `tests/ct/**` | Component rendering + interaction |
| E2E | `tests/e2e/**` | Full flows: FE-LAND → FE-QUIZ → FE-RESULT |
| Security | `*.spec.tsx` | FE-SEC-* |
| Accessibility | `__a11y__/a11y.smoke.spec.tsx` | FE-A11Y-* |

