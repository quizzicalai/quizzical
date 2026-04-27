# Quizzical AI — UX Design Specification

> This document specifies the user experience objectives, design system, user journeys, interaction patterns, and accessibility requirements for the Quizzical AI frontend.
>
> **Design ownership note:** The visual design system (colors, typography, spacing, layout dimensions) is *owned by the backend* and served dynamically via the `/config` endpoint as CSS design tokens. This document specifies *intent and constraints*; exact values live in `backend/appconfig.local.yaml` under `quizzical.frontend.theme`.

---

## Table of Contents

1. [UX Objectives & Design Philosophy](#1-ux-objectives--design-philosophy)
2. [User Personas](#2-user-personas)
3. [User Journeys](#3-user-journeys)
4. [Design System](#4-design-system)
5. [Page-by-Page UX Specification](#5-page-by-page-ux-specification)
6. [Interaction Patterns](#6-interaction-patterns)
7. [Loading & Waiting States](#7-loading--waiting-states)
8. [Error Handling UX](#8-error-handling-ux)
9. [Responsive Design](#9-responsive-design)
10. [Accessibility Design](#10-accessibility-design)
11. [Content & Copy Guidelines](#11-content--copy-guidelines)
12. [Backend-Owned Design Tokens](#12-backend-owned-design-tokens)
13. [UX Acceptance Criteria](#13-ux-acceptance-criteria)

---

## 1. UX Objectives & Design Philosophy

### Core Objectives

1. **Minimal friction to discovery.** A user should be able to start a quiz within 15 seconds of landing on the page — one input, one button, no signup required.
2. **Delight through expectation management.** AI generation takes time. The waiting experience should feel active and magical, not broken. Animated narration and a whimsical visual identity carry the brand during latency.
3. **Shareable results.** The final result should feel personal and worth sharing. The result page is the emotional climax of the experience.
4. **Backend-controlled presentation.** Operators can fully retheme and recopy the application via YAML without any frontend deployment.

### Design Philosophy

- **Approachable, not corporate.** Soft indigo/slate palette with playful accents. The wizard cat mascot (WizardCatIcon) conveys intelligence + whimsy.
- **Content-first layout.** The hero card is the primary focus on every page. Navigation and chrome are minimal — the app name and mascot in the header, minimal footer.
- **Progressive disclosure.** The quiz flow reveals only what is needed: synopsis first, then one question at a time. No quiz-wide progress bar until questions begin.
- **Trust through transparency.** Turnstile CAPTCHA is invisible (auto-executes); users are never blocked by a challenge unless something is wrong.
- **Calm under load.** Errors are presented plainly with recovery options (retry or start over), never with alarming red banners. The tone remains warm.

### Design Constraints

- All visual design tokens are configured from the backend — the frontend must not hardcode brand-specific colors, fonts, or layout dimensions.
- The app must function at 100% feature parity with JavaScript enabled; no SSR.
- No dark mode UI currently — class-based dark mode infrastructure exists but is not activated.

---

## 2. User Personas

### Primary: The Curious Explorer
- **Description**: Discovers the app via social share or direct link. Has a topic in mind (a TV show, a personality system, a fandom).
- **Goal**: Complete a fun personality quiz and see a result that feels accurate and shareable.
- **Behavior**: Mobile-first, short attention span, will abandon if loading takes >30s without feedback.
- **Pain points**: Unexpected errors, confusing question phrasing, results that feel generic.

### Secondary: The Returning Sharer
- **Description**: Has completed a quiz before. Returns via a shared result link or revisits to try a different topic.
- **Goal**: View a previously shared result or quickly try a new topic.
- **Behavior**: Lands on `/result/:id` (cold load) or immediately navigates to `/` and types a topic.
- **Pain points**: Broken result links, slow cold loads.

### Tertiary: The Operator / Administrator
- **Description**: A developer or content administrator deploying Quizzical for their own brand or community.
- **Goal**: Customize the visual theme and all user-facing copy without touching frontend code.
- **Behavior**: Edits `backend/appconfig.local.yaml` under `quizzical.frontend`.
- **Pain points**: Undocumented config keys, CSS token mismatches, unclear which strings are configurable.

---

## 3. User Journeys

### 3.1 Primary Journey: Discover → Quiz → Share

```
Landing page
  │
  ├─ User types topic ("Gilmore Girls")
  ├─ Invisible Turnstile auto-executes (silent, no friction)
  ├─ User clicks "Create My Quiz"
  │
  ▼
Loading state (quiz startup ~5–30s)
  ├─ WizardCat + SuperBalls animation
  ├─ Narration cycling: "Thinking…" → "Researching…" → "Generating questions…"
  │
  ▼
Synopsis view (optional)
  ├─ Quiz title + topic summary
  ├─ Character cards (who they might become)
  ├─ "Start Quiz" button
  │
  ▼
Question flow (6–15 questions)
  ├─ One question at a time
  ├─ 2–6 answer options as answer tiles (image + text)
  ├─ Tap to answer → brief spinner on tile → next question loads
  ├─ Loading narration between questions
  │
  ▼
Result page
  ├─ Profile title (h1) auto-focused
  ├─ Profile image (if available)
  ├─ Personality summary paragraph
  ├─ Trait list (e.g., "Creativity: High")
  ├─ Share button (native share → clipboard fallback)
  ├─ "Start Another Quiz" link
  ├─ Feedback section: 😄 / 😕 → optional comment → submit
```

### 3.2 Secondary Journey: Cold Load Result

```
User opens shared URL: /result/abc123
  │
  ▼
Result page (loading state)
  │
  ├─ API: GET /result/abc123
  ├─ Success → result displayed (same as above)
  └─ 404 → "This result could not be found" error + home button
```

### 3.3 Tertiary Journey: Session Recovery

```
User is mid-quiz → accidentally closes tab / refreshes
  │
  ▼
/quiz route loads with no store quizId
  │
  ├─ recoverFromSession() checks sessionStorage
  ├─ Valid session (< 1 hour old) → restore quiz state → resume polling
  └─ No session / expired → redirect to /
```

### 3.4 Error Recovery Journeys

```
Network error on quiz start:
  Landing → error message inline → user retries or edits topic

Polling timeout:
  Quiz flow → GlobalErrorDisplay (inline) → "Try Again" button → polling restarts

Unrecoverable error:
  Any page → GlobalErrorDisplay → "Start Over" → redirect to /
```

---

## 4. Design System

### 4.1 Color Palette

The color system uses semantic names mapped to CSS variables containing RGB triplets. The exact values are controlled by the backend; the frontend interprets them as `rgb(R G B / alpha)`.

#### Semantic Color Roles

| Role | CSS Var | Default (RGB) | Usage |
|------|---------|---------------|-------|
| **primary** | `--color-primary` | `0 121 174` (cyan-blue) | Main interactive color; buttons, links, focus rings, answer tile selection |
| **secondary** | `--color-secondary` | `30 41 59` (slate-800) | Header/footer backgrounds, dark surface text |
| **accent** | `--color-accent` | `234 179 8` (amber) | Highlight/decorative accent; underline decoration on landing title |
| **compliment** | `--color-compliment` | `0 121 174` (cyan) | Secondary accent; complements primary |
| **bg** | `--color-bg` | `238 242 255` (indigo-50) | Page background |
| **card** | `--color-card` | `255 255 255` (white) | Card / surface background |
| **fg** | `--color-fg` | `15 23 42` (slate-900) | Primary text |
| **border** | `--color-border` | `226 232 240` (slate-200) | Borders, dividers |
| **muted** | `--color-muted` | `148 163 184` (slate-400) | Secondary text, placeholders, subtle labels |
| **ring** | `--color-ring` | `129 140 248` (indigo-400) | Focus rings (accessibility) |
| **neutral** | `--color-neutral` | `148 163 184` (slate-400) | Neutral UI elements |

#### Color Usage Rules

- **Never hardcode hex values** in components. Always use Tailwind semantic classes (`text-primary`, `bg-card`) or CSS variables directly.
- **Interactive states**: hover uses opacity variant of primary (`rgb(var(--color-primary)/0.1)` backgrounds); active states reduce opacity or scale the element.
- **Error states**: use system red (`red-500` / `red-600`) — not a theme color, intentionally fixed.
- **Success states**: green confirmation (`green-600`) — not a theme color.
- **Turnstile bypass / dev indicators**: not shown to users.

### 4.2 Typography

Typography is also backend-controlled, but the *structure* of heading levels is fixed in the component tree:

| Use | Element | CSS Var | Default |
|-----|---------|---------|---------|
| Landing hero title | `<h1>` | `--font-size-landingTitle` | `2.25rem` |
| Landing subtitle | `<p>` | `--font-size-landingSubtitle` | `1.125rem` |
| Quiz synopsis title | `<h1>` | Tailwind `text-2xl sm:text-3xl` | — |
| Quiz question text | `<h2>` | Tailwind `text-xl sm:text-2xl` | — |
| Result profile title | `<h1>` | Tailwind `text-2xl sm:text-3xl` | — |
| Body text | `<p>` | `--font-size-body` | `1rem` |
| Button text | `<button>` | `--font-size-button` | `1rem` |
| Input text | `<input>` | `--font-size-input` | `1rem` |

**Font families**:
- Body: `--font-body` → default `Inter, sans-serif` (loaded via Google Fonts)
- Display/headings: `--font-display` → default `Nunito, sans-serif` (loaded via Google Fonts)

**Font weight guidelines**:
- Headings: `font-bold` (700)
- Body text: `font-normal` (400)
- Subtitles: `font-medium` (500)
- Labels / muted: `font-normal text-muted`

### 4.3 Spacing & Layout

All spacing uses Tailwind's default 4px scale. Key structural constraints:

- **Max content width**: `max-w-3xl` (~768px) for the hero card and quiz flow
- **Page padding**: responsive top padding from `--lp-pagePt*` tokens; horizontal `px-4 sm:px-6`
- **Card**: rounded corners from `--lp-cardRadius` (default `1rem`); shadow from `--lp-cardShadow`
- **Form max width**: `--lp-formMaxW` (default `36rem`)
- **Answer grid**: 2-column desktop, 1-column mobile, gap-4

### 4.4 Elevation & Depth

- **Page background**: flat `bg-bg`
- **Hero card**: box shadow from `--lp-cardShadow`; `bg-card`
- **Answer tiles**: hover: `shadow-md` + `translateY(-2px)`; default: `shadow-sm`
- **Blob**: decorative ambient glow behind hero icon; `opacity: --lp-blobOpacity` (default 0.18)
- **No modals or overlays** in the current design; all interactions are inline

### 4.5 Iconography

- **App mascot**: `WizardCatIcon` — wizard cat SVG; appears in header (small) and landing hero card (large)
- **Submit arrow**: `ArrowIcon` — directional arrow used by landing submit `IconButton`
- **Share**: `ShareIcon` — standard share icon
- **Arrow**: `ArrowIcon` — for CTA buttons
- Icons are pure SVG components; no icon font or sprite sheet
- All icons in interactive contexts must have an accessible label

### 4.6 Motion & Animation

| Element | Animation | Trigger |
|---------|-----------|---------|
| Loading spinner (WhimsySprite) | SuperBalls pulsing animation | During all loading states |
| Loading narration text | Fade + slide transition (typewriter effect) | On text change |
| Answer tile hover | `translateY(-2px)` + shadow increase | CSS hover |
| Answer tile active | `scale(0.98)` | CSS active |
| Answer tile selected | Ring highlight + background overlay | On click; persists until next question |
| Page transitions | Scroll to top | On route change |
| Image load | Fade-in opacity 0→1 | On `onLoad` event |
| Error display | Appears inline (no slide animation) | On error state |

**Motion preferences**: The current implementation does not explicitly implement `prefers-reduced-motion`. Future consideration: freeze WhimsySprite and skip narration transitions if reduced motion is preferred.

---

## 5. Page-by-Page UX Specification

### 5.1 Landing Page (`/`)

**Purpose**: The entry point. Convert curiosity into a quiz start.

**Layout** (top to bottom):
1. **Header**: App logo (WizardCat small) + app name → click navigates home
2. **Hero card** (max width `--lp-cardMaxW`, centered):
   - Hero icon: large WizardCatIcon with color blob behind it
   - Animated accent underline below title
   - **Title**: `content.landingPage.title` (default: "Discover Your True Personality")
   - **Subtitle**: `content.landingPage.subtitle`
   - **Topic input pill**: text field + submit button in a rounded pill container
     - Placeholder: `content.landingPage.placeholder`
     - Submit button: circular, `--lp-submitSize` (default 2.5rem), primary color, ArrowIcon (rendered via IconButton)
     - Input height: `--lp-inputHeight` (default 2.5rem)
     - On focus: ring glow (`--color-ring` at `--lp-ringAlpha` opacity)
   - **Validation error**: shown below input; configurable messages
   - **Invisible Turnstile**: auto-executes silently (no UI when enabled)
3. **Footer**: minimal links (About, Terms, Privacy, Donate) + copyright

**States**:
| State | Visual |
|-------|--------|
| Idle | Input enabled, submit button enabled |
| Submitting | Input disabled, submit button shows spinner |
| Error | InlineError below input with message |
| Turnstile failed | InlineError "Please complete security check" |

**Key UX decisions**:
- Invisible Turnstile: user never sees a CAPTCHA challenge in the happy path
- No email/login required: reduces friction to zero
- Input pill design: suggests a search bar metaphor familiar to users
- Examples in placeholder: guides users who don't know what to type

**AC-UX-LAND-1**: First interactive element on load is the topic input (visible, not pre-focused to avoid keyboard pop on mobile)
**AC-UX-LAND-2**: Submit button disabled while submitting (prevents double-submit)
**AC-UX-LAND-3**: Validation error appears below input, not as a modal
**AC-UX-LAND-4**: Page does not scroll horizontally at any viewport width
**AC-UX-LAND-5**: Hero card is visible above the fold on all common mobile viewports (375px width, 667px height)

---

### 5.2 Loading State (During Quiz Startup)

**Trigger**: After successful `POST /quiz/start`; while polling for first question/synopsis

**Layout**:
- Same hero card wrapper as landing
- **WhimsySprite** (SuperBalls animated loader) centered, colored with `--color-primary`
- **LoadingNarration**: cycling text below the loader
  - Default cycle: `"Thinking…"` → `"Researching topic…"` → `"Determining personality types…"` → `"Writing profiles…"` → `"Preparing topic…"`
  - In-quiz cycle: `"Thinking…"` → `"Planning quiz…"` → `"Generating questions…"` → `"Refining options…"`

**UX goals**:
- Convey active processing (not frozen/broken)
- Entertain during a necessary wait (up to 30s)
- Match brand personality (whimsical, intelligent)

**AC-UX-LOAD-1**: Loading narration begins within 100ms of navigation to `/quiz`
**AC-UX-LOAD-2**: At least one narration text change occurs within 5s
**AC-UX-LOAD-3**: Loader color matches `--color-primary` computed at render time
**AC-UX-LOAD-4**: Loading state has `role="status"` and `aria-live="polite"` for screen readers

---

### 5.3 Synopsis View (Optional)

**Trigger**: When `initialPayload` from `POST /quiz/start` contains a `type: 'synopsis'` payload

**Layout** (top to bottom within hero card):
1. **Topic title** (`<h1>`) — large, bold
2. **Hero image** (if `synopsis.imageUrl` present) — full-width rounded, with Image fallback
3. **Summary text** (`<p>`) — topic overview
4. **Character cards** (if `characters[]` present):
   - Grid: 1-col mobile, 2-col md+
   - Each card: optional character image + name (`<h3>`) + short description + profile text snippet
   - Subtle separator/border between character cards
5. **"Start Quiz" CTA button** (primary color, full-width on mobile)
6. **"Start Over" link** (muted, below CTA)

**UX goals**:
- Set context and expectations before the questions begin
- Build emotional investment by showing possible result characters
- Allow users to change their mind (Start Over) without commitment

**AC-UX-SYN-1**: "Start Quiz" button is below the fold only on very long synopsis pages; users can see the button without scrolling on mobile at 375px width when synopsis has ≤2 characters
**AC-UX-SYN-2**: Character images that fail to load show a neutral placeholder (never broken icon)
**AC-UX-SYN-3**: "Start Over" is visually secondary (lower contrast) vs "Start Quiz" primary CTA

---

### 5.4 Question Flow (`/quiz`)

**Trigger**: After synopsis "Start Quiz" click, or when first question is ready from polling

**Layout** (within hero card):
1. **Progress indicator**: `"Question {n} of {total}"` — small text above question (muted color)
2. **Question text** (`<h2>`) — auto-focused on new question; large, prominent
3. **Optional question image** — if `question.imageUrl` present
4. **Answer grid**:
   - 2-column layout on desktop; 1-column on mobile
   - Odd number of answers: last tile spans both columns, centered, max-width constrained
   - Each tile: optional image (top half) + answer text (bottom)
5. **InlineError** (if answer submission failed) — below the grid with retry

**States per answer tile**:
| State | Visual |
|-------|--------|
| Default | White card, subtle border, shadow-sm |
| Hovered | Lifts 2px, shadow-md |
| Active (press) | Scale 0.98 |
| Selected (pending) | Ring border + primary color overlay tint + Spinner sm |
| Disabled (other tiles) | Opacity 60%; cursor not-allowed |

**UX goals**:
- Answer tap = immediate feedback (visual response within 16ms)
- After tap: all other tiles disabled; selected tile shows spinner
- Seamless transition to next question (loading narration appears, then new question)
- Progress counter manages expectations about quiz length

**AC-UX-Q-1**: Answer tile becomes visually selected immediately on click (before API responds)
**AC-UX-Q-2**: All other tiles disabled immediately when one tile is selected
**AC-UX-Q-3**: New question: h2 auto-focused, scroll to top of card
**AC-UX-Q-4**: Progress counter visible and correct at each question
**AC-UX-Q-5**: Answer tile images are square/uniform; text tiles are uniform height within a row

---

### 5.5 Result Page (`/result` or `/result/:resultId`)

**Purpose**: The emotional payoff. Display the user's personality result and encourage sharing.

**Layout**:
1. **Profile title** (`<h1>`) — auto-focused on mount; e.g., "You are Lorelai Gilmore"
2. **Result image** (if `imageUrl` present) — full-width rounded top; graceful fallback
3. **Personality summary** (`<p>`) — 2–3 sentences
4. **Trait list** (if `traits[]` present):
   - Section heading: `content.resultPage.traitListTitle` (default: "Your Traits")
   - Each trait: `{label}: {value}` in a list
5. **Share section**:
   - Share button: `content.resultPage.shareButton` label
   - Native Web Share API first; clipboard copy fallback
   - After share: `content.resultPage.shared` acknowledgement
   - Copy link: `content.resultPage.copyLink` label shown as fallback
6. **"Start Another Quiz"** link/button: navigates to `/`
7. **Feedback section** (below result card):
   - Prompt: `content.resultPage.feedback.prompt`
   - Thumbs up (😄) / thumbs down (😕) oval buttons
   - On rating: optional comment textarea appears
   - Submit: POST `/feedback` with Turnstile verification
   - On success: `content.resultPage.feedback.thanks` message

**States**:
| State | Visual |
|-------|--------|
| Loading (cold load) | LoadingCard with narration |
| Loaded | Full result display |
| Error | GlobalErrorDisplay (inline variant) |

**AC-UX-RES-1**: Profile title (h1) receives focus on result page mount (enables screen reader announcement)
**AC-UX-RES-2**: Share button uses native Web Share API when available; falls back to clipboard silently
**AC-UX-RES-3**: "Start Another Quiz" is always visible without scrolling on desktop
**AC-UX-RES-4**: Feedback section does not block viewing the result (positioned after result)
**AC-UX-RES-5**: Feedback can only be submitted once per page load (button disabled after submit)

---

### 5.6 Static Pages (`/about`, `/terms`, `/privacy`)

All three static pages share the same `StaticPage` component renderer.

**Layout**:
1. **Title** (`<h1>`) — from `content.{page}.title`
2. **Description** (`<p>`) — from `content.{page}.description` (if present)
3. **Content blocks** (if `blocks[]` present) — rendered by `BlockRenderer`:
   - `type: 'p'` → `<p>` paragraph
   - `type: 'h2'` → `<h2>` section heading
   - `type: 'ul'` → unordered list with bullets
   - `type: 'ol'` → ordered list with numbers

**UX goals**:
- Operator-configurable content without frontend deployment
- Clean typography; readable line length (max ~75 chars)

**AC-UX-STATIC-1**: Static pages render with no content when `blocks` is absent (title + description only)
**AC-UX-STATIC-2**: All content strings are HTML-escaped (no XSS via config)

---

### 5.7 Error Page

**Triggered by**: `ErrorPage` component (route error) or `GlobalErrorDisplay` (component-level errors)

**Layout**:
- Centered heading + message
- One or two action buttons (Retry / Start Over / Go Home)

**Design principles**:
- Tone: calm and helpful, not alarming
- Never show technical error details (stack traces, API response bodies) to end users
- Always provide a clear next action

**AC-UX-ERR-1**: Error page shows at minimum one clear call to action (not just a message)
**AC-UX-ERR-2**: Error messages match the configured `content.errors.*` strings
**AC-UX-ERR-3**: Retry button shown only when error is retriable (server error or timeout)

---

### 5.8 404 Page (`*`)

**Layout**:
- Heading: `content.notFoundPage.heading` (default: "404")
- Subheading: `content.notFoundPage.subheading`
- Button: `content.notFoundPage.buttonText` (navigates to `/`)

**AC-UX-404-1**: 404 page always has a visible link/button back to home

---

## 6. Interaction Patterns

### 6.1 Primary Button

- Full border-radius (pill or rounded-xl depending on context)
- Primary color background (`rgb(var(--color-primary))`)
- White text
- Hover: darken via opacity reduction or `brightness(0.9)`
- Active: scale(0.97)
- Disabled: opacity 40%, `cursor-not-allowed`
- Minimum touch target: 44px height (WCAG 2.5.5)

### 6.2 Input Pill (Landing Form)

- Pill shape: `border-radius: 9999px`
- Background: `--lp-pillBg` (semi-transparent card color)
- Border: `--lp-pillBorder` (muted color with opacity)
- On focus: ring glow using `--color-ring` at `--lp-ringAlpha`
- Submit button: perfectly circular (equal width + height = `--lp-submitSize`), primary color, no border
- Input + button share a single pill container (visually unified)

### 6.3 Answer Tiles

- Card-style tiles (bg-card, border, shadow-sm)
- Uniform height within a row (CSS flexbox or grid stretch)
- Image occupies top ~60% of tile when present; text below
- When no image: text vertically centered
- Selected state: primary color ring (`outline: 2px solid rgb(var(--color-primary))`) + very light primary color overlay (5–10% opacity)
- Spinner center-aligned over tile content when selected + awaiting response

### 6.4 Share Flow

1. Click share button
2. Try `navigator.share({ title, text, url })` (Web Share API)
3. If user cancels share → no action; button resets
4. If `navigator.share` unavailable → `navigator.clipboard.writeText(url)`
5. On clipboard success → button changes to `content.resultPage.shareCopied` text for 2s
6. On clipboard failure → show error (rare)

### 6.5 Feedback Flow

1. Show 😄 and 😕 buttons
2. On tap → selected icon highlighted; optional comment textarea animates in
3. Comment is optional (max 4096 chars via backend validation)
4. Turnstile verification before submit (if `features.turnstile = true`)
5. Submit button disabled until Turnstile token received
6. On success → hide buttons + textarea; show `feedback.thanks` message
7. No second submission allowed (state `submitted = true`)

### 6.6 Session Recovery Banner

When `sessionRecovered = true` in store: subtle informational note may be shown (implementation-dependent; current behavior resumes silently without a banner).

---

## 7. Loading & Waiting States

### Loading State Hierarchy

| Scenario | Component | Duration |
|----------|-----------|----------|
| App config loading | `ConfigProvider` renders null or spinner | < 2s typical |
| Quiz startup | `LoadingCard` (WhimsySprite + narration) | 5–30s |
| Between questions | `LoadingCard` (in-quiz narration) | 2–10s |
| Cold result load | `LoadingCard` | 1–5s |

### Loading Narration Timing

Narration text changes at configured `atMs` timestamps. Current defaults:

**Pre-quiz loading** (startup):
| Time (ms) | Text |
|-----------|------|
| 0 | "Thinking…" |
| 3000 | "Researching topic…" |
| 6000 | "Determining personality types…" |
| 9000 | "Writing profiles…" |
| 12000 | "Preparing topic…" |

**In-quiz loading** (between questions):
| Time (ms) | Text |
|-----------|------|
| 0 | "Thinking…" |
| 3000 | "Planning quiz…" |
| 6000 | "Generating questions…" |
| 9000 | "Refining options…" |

### Progress Feedback

- **Questions**: `"Question {n} of {total}"` counter (visible, no animation)
- **Startup**: loading narration only (no progress bar; total unknown until first question)
- **Polling timeout**: if loading exceeds `poll.total` (60s), error state shown (see §8)

---

## 8. Error Handling UX

### Error Tone & Language

- **Never** expose internal error codes, stack traces, or API error bodies to users
- **Always** use configured `content.errors.*` strings
- **Retriable errors** (network, timeout, server 5xx): show "Try Again" option
- **Non-retriable errors** (404, auth, validation): show "Start Over" option
- **Tone**: plain, calm, actionable (avoid "Something went terribly wrong")

### Error Display Variants

| Variant | Usage | Position |
|---------|-------|----------|
| `InlineError` | Single-field validation; quick inline feedback | Directly below affected element |
| `GlobalErrorDisplay (inline)` | Non-fatal errors within quiz flow | Replaces current view content |
| `GlobalErrorDisplay (page)` | Fatal page-level errors | Full page centered |
| `GlobalErrorDisplay (banner)` | Background errors that don't block interaction | Top of page (not yet used) |
| `ErrorBoundary fallback` | Uncaught JavaScript exceptions | Full page replacement |

### Error Message Map

| Condition | Message Key | Default Text |
|-----------|-------------|--------------|
| Generic error | `errors.title` / `errors.description` | "An Error Occurred" / generic description |
| Request timeout | `errors.requestTimeout` | "The request timed out." |
| Quiz creation failed | `errors.quizCreationFailed` | "We were unable to create your quiz at this time." |
| Category not found | `errors.categoryNotFound` | "Sorry, we couldn't create a quiz for that category." |
| Result not found | `errors.resultNotFound` | "This result could not be found." |
| Generic start over | `errors.startOver` | "Start Over" |
| Retry | `errors.retry` | "Try Again" |
| Go home | `errors.home` | "Go Home" |

---

## 9. Responsive Design

### Breakpoints (Tailwind defaults)

| Breakpoint | Min width | Usage |
|------------|-----------|-------|
| (mobile base) | 0px | Default styles |
| `sm:` | 640px | Larger mobile / landscape |
| `md:` | 768px | Tablet |
| `lg:` | 1024px | Desktop |
| `xl:` | 1280px | Wide desktop (not specifically targeted) |

### Per-Page Responsive Behavior

#### Landing Page
- Card: max-width constrained (`--lp-cardMaxW`); centered; full-width on small screens
- Hero icon: height scales from `--lp-heroHSm` (mobile) → `--lp-heroHLg` (desktop)
- Page top padding: `--lp-pagePtSm` (mobile) → `--lp-pagePtLg` (desktop)
- Title: `--font-size-landingTitle` (no responsive override; token handles it)
- Form: max-width `--lp-formMaxW`; full-width on small screens

#### Quiz Flow
- Hero card: same max-width as landing; padding responsive
- Answer grid: 1-col (mobile) → 2-col (sm:)
- Synopsis characters: 1-col (mobile) → 2-col (md:)

#### Result Page
- Result image: full-width rounded within card
- Trait list: single column on all viewports
- Feedback buttons: centered, uniform size

#### Header / Footer
- Header: fixed height; logo left-aligned
- Footer: hamburger nav toggle on mobile (`< sm`); horizontal nav on desktop

### Touch Targets
- Minimum 44×44px for all interactive elements (WCAG 2.5.5)
- Answer tiles: minimum 48px tall to ensure easy tap
- Icon buttons: `sm`=40px, `md`=44px, `lg`=48px (enforced by `IconButton` component)

---

## 10. Accessibility Design

### WCAG 2.1 AA Compliance Goals

- **Color contrast**: Primary color on white must be ≥ 4.5:1 (AA). Muted text on white must be ≥ 3:1 (large text AA).
- **Focus management**: Focus is programmatically moved to h1/h2 on view transitions.
- **Keyboard navigation**: All interactive elements reachable by Tab; Enter activates buttons.
- **Screen reader support**: All decorative images have `alt=""`; functional images have descriptive alt text.
- **No keyboard traps**: Tab sequence flows naturally; no focus trap except if a modal were added (currently none).

### Focus Management Rules

| Event | Focus target |
|-------|--------------|
| New question loaded | `<h2>` of question text |
| Result displayed | `<h1>` of profile title |
| Error displayed | First focusable element in error component |
| Navigation (route change) | `<main>` element (`tabindex="-1"` then focus) via `ScrollAndFocusManager` |

### ARIA Landmark Regions

```html
<header role="banner">      <!-- Header component -->
<main>                       <!-- Router outlet -->
<footer role="contentinfo">  <!-- Footer component -->
```

### ARIA Live Regions

| Component | Role/Live | Updates |
|-----------|-----------|---------|
| LoadingNarration | `role="status"`, `aria-live="polite"` | Loading text changes |
| InlineError | `role="alert"` | Error messages |
| GlobalErrorDisplay | `role="alert"`, `aria-live="assertive"` | Error display |

### Semantic HTML Rules

- Headings use sequential hierarchy: one `h1` per page; `h2` for question; `h3` for character names
- Lists use `<ul>`/`<ol>` + `<li>` (not `<div>` lists)
- Buttons use `<button>` (never `<div onClick>`)
- Navigation uses `<nav>` with `aria-label`
- Footer nav toggle: `aria-expanded`, `aria-controls`, `aria-haspopup="true"`

### Accessibility Smoke Tests

All critical pages pass axe automated scan with zero violations:
- Landing page (idle state)
- Quiz flow page (question state)
- Result page
- Error page
- 404 page

---

## 11. Content & Copy Guidelines

### Voice & Tone

- **Warm and encouraging**: "Discover Your True Personality" — not "Take our quiz"
- **Concise**: Loading messages < 30 chars; error messages < 80 chars
- **Action-oriented**: CTAs use verbs ("Create My Quiz", "Start Quiz", "Share your result")
- **Human**: "Sorry, we couldn't create a quiz for that category." — not "Error 422: invalid category"

### Configurable vs. Fixed Copy

All user-visible copy (except form validation messages rendered inline by components) is configurable via `backend/appconfig.local.yaml` under `quizzical.frontend.content`.

| Content section | Config path |
|-----------------|-------------|
| Landing title, subtitle, placeholder | `content.landingPage.title/subtitle/placeholder` |
| Button labels | `content.landingPage.buttonText`, `content.resultPage.*Button` |
| Footer links | `content.footer.*` |
| Static page content | `content.aboutPage/termsPage/privacyPolicyPage.blocks[]` |
| Error messages | `content.errors.*` |
| Loading messages | `content.loadingStates.*` |
| Result labels | `content.resultPage.*` |
| Feedback labels | `content.resultPage.feedback.*` |

### Validation Messages

Input validation messages use `{min}` and `{max}` as interpolation tokens:
- `content.landingPage.validation.minLength` → "Must be at least {min} characters."
- `content.landingPage.validation.maxLength` → "Cannot exceed {max} characters."

The frontend substitutes the actual `category_min_length` / `category_max_length` values at render time.

### Copy Principles

- **No placeholder copy in production**: all static page content fields (`aboutPage.description`, `termsPage.description`) must be populated before launch
- **Character limit hints**: placeholder text should give realistic examples (`"e.g., Gilmore Girls, Myers Briggs, 90s R&B..."`)
- **Feedback labels**: emoji are intentional brand choices (😄 / 😕) — accessible via `aria-label` overrides

---

## 12. Backend-Owned Design Tokens

> This is the critical design authority section. The backend YAML is the single source of truth for all visual design. Changing colors, fonts, or layout requires editing `backend/appconfig.local.yaml` under `quizzical.frontend.theme` — **not** any frontend file.

### Complete Token Reference

The backend serves these tokens via `GET /config`. Frontend `ThemeInjector` maps them to CSS variables.

#### Color Tokens (`theme.colors`)

| YAML key | Injected as | Purpose |
|----------|-------------|---------|
| `primary` | `--color-primary` | Primary interactive color |
| `secondary` | `--color-secondary` | Dark surface backgrounds |
| `accent` | `--color-accent` | Decorative accent |
| `compliment` | `--color-compliment` | Secondary accent |
| `bg` | `--color-bg` | Page background |
| `card` | `--color-card` | Card/surface background |
| `fg` | `--color-fg` | Primary text |
| `border` | `--color-border` | Borders and dividers |
| `muted` | `--color-muted` | Secondary text, placeholders |
| `ring` | `--color-ring` | Focus rings |
| `neutral` | `--color-neutral` | Neutral UI elements |

**Format**: RGB triplet string `"R G B"` (e.g., `"0 121 174"`). Hex values (e.g., `"#0079ae"`) also accepted — the frontend normalizes via `toRgbTriplet()`.

#### Font Tokens (`theme.fonts`)

| YAML key | Injected as | Purpose |
|----------|-------------|---------|
| `sans` | `--font-body` | Body text / UI text |
| `serif` | `--font-display` | Display / heading text |

**Format**: CSS font-family string (e.g., `"Inter, sans-serif"`)

#### Font Size Tokens (`theme.fontSizes`)

| YAML key | Injected as | Purpose |
|----------|-------------|---------|
| `body` | `--font-size-body` | Body text |
| `input` | `--font-size-input` | Input fields |
| `button` | `--font-size-button` | Button labels |
| `landingTitle` | `--font-size-landingTitle` | Landing hero title |
| `landingSubtitle` | `--font-size-landingSubtitle` | Landing subtitle |

**Format**: CSS size string (e.g., `"1rem"`, `"2.25rem"`)

#### Landing Layout Tokens (`theme.layout.landing`)

All 33 tokens; injected as `--lp-{key}`. See §11.4 in `frontend-design.MD` for full table.

**Format**: CSS value string with units (e.g., `"1.5rem"`, `"56rem"`, `"1px solid rgba(var(--color-muted), 0.55)"`)

### Operator Theming Guide

To retheme the application:
1. Open `backend/appconfig.local.yaml`
2. Find `quizzical.frontend.theme`
3. Update color values as RGB triplets
4. Update font values as CSS font-family strings
5. Update font size and layout tokens as CSS dimension strings
6. Restart or wait for config cache to expire (default 60s)
7. Browser refresh reflects new theme immediately

### Token Constraints for Designers

- **Do not** use pure black (`0 0 0`) for `fg` — use near-black slate for softness
- **Do not** use pure white (`255 255 255`) for `bg` — use a very light tint for depth
- **Do** ensure primary/fg contrast meets WCAG AA (4.5:1) on the card background
- **Do** keep accent visually distinct from primary (different hue recommended)
- Layout tokens use CSS units — always include unit suffix (`rem`, `px`, etc.)
- Blob opacity token (`blobOpacity`) should remain < 0.3 to stay decorative, not distracting

---

## 13. UX Acceptance Criteria

### User Journey ACs

| AC | Scenario | Expected |
|----|----------|----------|
| AC-UX-J-1 | Landing → submit valid topic → navigate to quiz | ≤ 2s to `/quiz` route; loading state shown immediately |
| AC-UX-J-2 | Quiz start → first question/synopsis | Shown within 30s; loading narration visible throughout |
| AC-UX-J-3 | Answer question → next question | ≤ 10s between answer and next question appearing |
| AC-UX-J-4 | Final answer → result | ≤ 15s to result display |
| AC-UX-J-5 | Result → share | Share action < 1s response |
| AC-UX-J-6 | Cold load `/result/:id` | Result displayed within 5s (network permitting) |
| AC-UX-J-7 | Session recovery (reload mid-quiz) | Resume from correct question within 5s |

### Visual Design ACs

| AC | Check | Expected |
|----|-------|----------|
| AC-UX-VIS-1 | Landing page CSS vars injected | `--color-primary` matches YAML value within 2s |
| AC-UX-VIS-2 | Card max width | Never wider than `--lp-cardMaxW` at any viewport |
| AC-UX-VIS-3 | Answer grid | 2-col at ≥ 640px; 1-col below 640px |
| AC-UX-VIS-4 | Input pill border radius | `9999px` (pill shape) |
| AC-UX-VIS-5 | Submit button | Circular; diameter = `--lp-submitSize` |
| AC-UX-VIS-6 | Blob opacity | Matches `--lp-blobOpacity` (default 0.18) |

### Interaction ACs

| AC | Interaction | Expected |
|----|-------------|----------|
| AC-UX-INT-1 | Click answer tile | Selected ring + overlay appear within 1 frame (< 16ms) |
| AC-UX-INT-2 | Other tiles when one selected | `opacity: 60%`, `cursor: not-allowed` |
| AC-UX-INT-3 | Submit button disabled state | `opacity: 40%`, `cursor: not-allowed`, not clickable |
| AC-UX-INT-4 | Focus ring on keyboard | Visible at all times when navigating by keyboard |
| AC-UX-INT-5 | Share copied confirmation | Text changes to `shareCopied` label; reverts after 2s |

### Content ACs

| AC | Condition | Expected |
|----|-----------|----------|
| AC-UX-COPY-1 | Backend config loaded | `content.landingPage.title` displayed instead of default |
| AC-UX-COPY-2 | Backend config unavailable | Default `DEFAULT_APP_CONFIG` text displayed; no broken strings |
| AC-UX-COPY-3 | Static page with blocks | Rendered correctly as h2, p, ul, ol elements |
| AC-UX-COPY-4 | Error message | Uses `content.errors.*` string; no raw API error detail |

### Accessibility ACs

| AC | Condition | Expected |
|----|-----------|----------|
| AC-UX-A11Y-1 | New question loads | h2 receives focus; screen reader announces question text |
| AC-UX-A11Y-2 | Result loads | h1 receives focus; screen reader announces result title |
| AC-UX-A11Y-3 | Error displayed | `role="alert"` element; screen reader announces error message |
| AC-UX-A11Y-4 | Loading state active | `aria-live="polite"` region announces narration text changes |
| AC-UX-A11Y-5 | axe scan on any page | Zero violations (Level AA) |
| AC-UX-A11Y-6 | Keyboard navigation | Tab order reaches all interactive elements; no skip; no trap |
| AC-UX-A11Y-7 | Answer tiles keyboard | Enter/Space activates selected tile |

### Operator Theming ACs

| AC | Condition | Expected |
|----|-----------|----------|
| AC-UX-THEME-1 | Update `primary` color in YAML | CSS var updates on next config load (≤ 60s cache) |
| AC-UX-THEME-2 | Update `content.landingPage.title` in YAML | Landing title updates within 60s |
| AC-UX-THEME-3 | Set `features.turnstile: false` in YAML | Turnstile widget never rendered; bypass token auto-emitted |
| AC-UX-THEME-4 | Set `category_max_length: 50` in YAML | Frontend-configured validation metadata and copy update from config; backend `POST /quiz/start` still enforces schema max length (100) |
| AC-UX-THEME-5 | Add `blocks[]` to `aboutPage` in YAML | About page renders block content |
