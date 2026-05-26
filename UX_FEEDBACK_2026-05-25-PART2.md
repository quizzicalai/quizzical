# UX Feedback — 2026-05-25 (Part 2)

Follow-up review after Part 1 ship (`dc58bda`). Author: product owner.

## Status

- [x] **1. Header persists on all pages.**
      Root cause: `AppRouter` wraps `<Routes>` inside `<Suspense>`, so
      while a lazy page chunk loads the **entire** `AppLayout`
      (header+main+footer) is replaced by the fallback. Fix: move the
      `<Suspense>` boundary INSIDE `AppLayout`, wrapping only
      `<Outlet />`. Header/Footer stay mounted across navigations.
- [x] **2. Footer locks to viewport bottom on short pages.** Footer
      vanishes during Suspense fallback (see #1) and on a few pages
      stays above the bottom because `<main>` is `flex-grow` but not
      itself a flex container, so the page-level wrappers' own
      `flex-grow` is a no-op. Fix: make `<main>` `flex flex-col`,
      remove the redundant `flex-grow` on inner wrappers, and let
      Footer's `mt-auto` do the work.
- [x] **3. WhimsySprite spins again during synopsis / quiz loading.**
      Regression from Part 1: `LoadingCard` renders `<WhimsySprite />`
      with no `spinning` prop, so the new idle-state SVG sits motionless
      while the agent is actually loading. Fix: `<WhimsySprite spinning />`.
- [x] **4. Top-right thinking indicator spins while loading, stays
      still otherwise, always visible.** Verified: `ThinkingIndicator`
      already renders the spinning variant (`animate-spin` orbit)
      when `thinking={true}` and the still two-dot variant when
      `false`, and `QuestionView` always renders the row (no
      conditional mounting). No code change required for this item;
      the Part 1 idle/spin toggle was already implementing the
      contract.
- [x] **5. Confidence score visible when NOT thinking.** Currently
      `QuestionView` appends `(N% confident)` only while `isLoading`.
      Swap: append confidence only when the question is on screen and
      the agent is idle.
- [x] **6. Playful "Hmmm…" pool while thinking; agent's progress
      phrase + confidence when idle.** Add ~150 short-form playful
      phrases (`Hmmm…`, `Interesting, let me think…`,
      `One sec — that's a juicy one…`) as `ACTIVE_THINKING_PHRASES`.
      Use this pool whenever `isLoading`; use the agent's
      `progress_phrase` (already on `question.progressPhrase`) when
      idle. Keep `FINALIZING_PHRASES` separate.
- [x] **7. Mobile menu three-dots glass effect.** `MenuButton` strips
      the border and bg on phones to read "as just the three dots."
      Result: insufficient contrast on light backgrounds → fails WCAG
      AA. Fix: keep a real glass surface on phones (backdrop-blur,
      `bg-card/85`, 1.5px border, soft shadow, primary-tinted icon).
- [x] **8. Feedback submit button + selection states.**
      a) Submit `disabled` when the comment textarea is empty
         (currently only requires rating + Turnstile).
      b) Selected rating button gets a thick (border-4) primary-color
         outline instead of the existing thin `border-muted/40` +
         ring. Hard-to-miss "this is the one I chose" affordance.
- [x] **9. Stray `*` after "result?"** in the feedback prompt.
      `FeedbackIcons` renders `<span className="text-error">*</span>`
      after the prompt as a "required" marker. Remove (`aria-required`
      on the radiogroup already conveys this semantically; the visible
      asterisk is noise next to a binary 👍/👎 picker).

## Tests

Each item gets a vitest unit/render test. Where behavior crosses
components (Suspense + layout), add a smoke test that asserts header
+ footer remain mounted across a route change.

- `AppLayout.spec.tsx` (NEW) — header/footer mounted before & after a
  Suspense-pending route swap; `<main>` is a flex column.
- `LoadingCard.spec.tsx` (NEW) — `whimsy-sprite[data-state="spinning"]`.
- `QuestionView.spec.tsx` — extend: confidence appears when idle and
  hides while loading; playful phrase pool used while loading; phrase
  pool size ≥ 100.
- `ThinkingIndicator.spec.tsx` — extend: visible in both states,
  always sits to the LEFT of the phrase span.
- `Footer.spec.tsx` — extend: mobile menu button has backdrop-blur +
  card surface + border on phones (no longer transparent).
- `FeedbackIcons.spec.tsx` — extend: submit disabled when comment
  empty; selected button gains `border-4 border-primary`; no `*`
  rendered.

## CI/CD smoke gap

Part 1's P0a (broken redis save) and this round's #3 (WhimsySprite
regression) both shipped to prod because the only smoke we run after
deploy is `prod_render_smoke.py` (loads landing) + `prod_precompute_smoke.py`
(seed import). Neither:

1. Starts a real quiz (would have caught P0a since `save_quiz_state`
   only fires after at least one answer).
2. Renders a question view in a real browser (would catch any visual
   regression in the spinner / status row / feedback UI).
3. Exercises Turnstile error paths.

Proposed additions (separate PR):

- **E2E smoke job** — Playwright run against the deployed dev SWA +
  prod backend that completes a full quiz: landing → submit topic →
  synopsis → "Get Started" → answer 1 question → answer until
  finished → view result → submit feedback. Run on every successful
  prod deploy; gate auto-rollback on failure.
- **Visual regression on key states** — Percy / Playwright snapshot:
  landing idle, landing preparing, quiz loading card, quiz question
  with confidence, feedback selected, feedback submitted. Catches the
  spinning/idle SVG swap regression class.
- **Health-endpoint structured-output assertion** — extend the
  existing render smoke to call an agent endpoint that exercises
  `_do_structured_response` so the Gemini schema gap (P1 from Part 1)
  surfaces in CI rather than in user traces.
