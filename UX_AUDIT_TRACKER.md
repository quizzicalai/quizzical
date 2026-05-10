# UX Audit Tracker (temporary)

Tracks the audit findings from the May 9, 2026 review. Each item carries the original severity tag from the report. Mark complete with `[x]` and add the commit SHA.

Legend: `[ ]` not started · `[~]` in progress · `[x]` done

---

## CRITICAL (3)
- [ ] **C1** Per-result OG image generation (share-time virality lever) _(SEO/Result)_
- [ ] **C2** Save-as-image / "share to story" on the result page _(Result)_
- [x] **C3** Per-route `<title>` manager (history/back-button context) — already implemented in `router/AppRouter.tsx:82` (DocumentTitleUpdater); covered by `AppRouter.spec.tsx`

## HIGH (12)
- [x] **H1** Replace hardcoded `red-*/gray-*/slate-*/green-*` literals with semantic tokens; add `--color-success` _(global)_ — `4e61881`
- [x] **H2** `HeroCard` `border-slate-200` → `border-border` — `HeroCard.tsx:24` — `4e61881`
- [x] **H3** `SynopsisView` primary CTA inline `style` → `bg-primary text-white` — `SynopsisView.tsx:61` — next commit
- [x] **H4** `text-red-600` literals in `QuizFlowPage`/`QuestionView` → semantic token — `4e61881`
- [x] **H5** `AnswerTile` selected state announces "currently selected" via `aria-label` (not just `aria-pressed`) — next commit
- [x] **H6** `FeedbackIcons` success uses `text-green-700`; introduce `text-success` token — `FeedbackIcons.tsx:77` — `4e61881`
- [x] **H7** Header `h-10` (below 44×44 touch target) → `h-12` minimum — `Header.tsx:14` — next commit
- [x] **H8** Strip `TODO: Sentry/LogRocket/App Insights` from `ErrorBoundary.tsx:82` — next commit
- [x] **H9** Unify funnel CTA copy ("Generate quiz" / "Create My Quiz" / "Start Quiz") — standardized on "Start Quiz" — next commit
- [ ] **H10** Global toast/snackbar pattern (replace ad-hoc copy/feedback success surfaces)
- [x] **H11** `og:image` absolute URL (LinkedIn/X crawlers) — `index.html:35` — vite plugin reads `VITE_PUBLIC_URL`
- [x] **H12** `<meta name="theme-color">` light + dark variants — already present in `index.html:13-14`

## MEDIUM (27)
- [x] **M1** Landing form error pill `bg-red-50/200/700` → semantic — `LandingPage.tsx:199` — `4e61881`
- [x] **M2** Verified already met: landing submit `IconButton size="md"` resolves to `w-11 h-11` (44×44) — commit `599fcc1`+
- [x] **M3** Landing input `maxLength` + visible counter past 70%, error tint at cap — next commit
- [ ] **M4** Synopsis "Try another topic" escape link — bolder hover + `underline-offset-4`
- [ ] **M5** `AnswerTile` image lazy-load skeleton (`animate-pulse`)
- [x] **M6** `AnswerTile` loading overlay `bg-white/50 dark:bg-black/40` → `bg-card/60` — `599fcc1`
- [ ] **M7** Keyboard shortcut hints (1–9 to pick, Enter to confirm)
- [x] **M8** Skipped — by design: agent ends quiz on confidence threshold OR max questions, so showing a denominator ("of 20") would mislead. Visible ordinal already exists at `quiz-question-ordinal`.
- [x] **M9** Feedback textarea char counter (cap 4096) with soft warn at 80% — next commit
- [x] **M10** Feedback submit shows inline spinner during request — next commit
- [x] **M11** Result traits grid handles `<= 2` traits (single column, no orphan) — next commit
- [x] **M12** "Play again" + "Try a new topic" CTA pair under share bar — `fc3d62d`
- [x] **M13** `ErrorBoundary` `text-destructive` undefined + `bg-gray-100/dark:bg-gray-800` → semantic — `4e61881`
- [x] **M14** `GlobalErrorDisplay` hardcoded red icon → CSS-variable driven — `4e61881`
- [x] **M15** `SkipLink` `focus:ring-fg` → `focus:ring-primary` — `4e61881`
- [ ] **M16** Safe-area insets (`env(safe-area-inset-bottom)`) on bottom-fixed elements
- [ ] **M17** `HeroCard` `min-h-[40vh]` → `35vh` <sm (iPhone landscape)
- [ ] **M18** `LoadingNarration` announce completion via `aria-live="polite"`
- [x] **M19** Feedback emoji buttons: visible labels ("Good" / "Needs work") — `fc3d62d`
- [ ] **M20** Per-result OG image (also tracked under C1)
- [x] **M21** `<link rel="canonical">` — vite plugin emits when `VITE_PUBLIC_URL` set
- [ ] **M22** Drop unused Baloo 2 Google Font; consider self-hosted variable fonts
- [ ] **M23** `ConfigProvider` 1.5s fallback to in-bundle defaults (avoid white-screen)
- [x] **M24** Strip `console.warn` in `QuizFlowPage` (wrap in `import.meta.env.DEV`) — verified all 6 calls already DEV-guarded
- [x] **M25** `ErrorPage`/`NotFoundPage` use `font-display`
- [ ] **M26** Empty state on landing recent-quizzes with SVG + nudge
- [ ] **M27** Required-field indicator (`*` / `aria-required`) on landing input + feedback textarea
- [ ] **M28** Turnstile failure: distinguish network vs timeout vs bot-blocked + retry
- [ ] **M29** Result hero image aspect-ratio reservation (CLS)
- [ ] **M30** Entrance animation on HeroCard + result page (200ms fade + slide-up)
- [ ] **M31** Feedback emoji `active:scale-95`

## POLISH (15)
- [x] **P1** Topic chips `cursor: pointer` + `hover` lift+scale (1.03) — next commit
- [ ] **P2** Landing subtitle drop `/90` opacity
- [ ] **P3** Synopsis: `truncate` on character names (<320px)
- [x] **P4** `QuestionView` italic help `text-slate-500` → `text-muted` — line 201 — `4e61881`
- [ ] **P5** Footer mobile menu rotate 90° → opacity/height under reduced-motion
- [ ] **P6** Result hero image preload (`<link rel="preload" as="image">` once result polls in)
- [ ] **P7** Disabled buttons `disabled:bg-muted disabled:text-muted/60 disabled:pointer-events-none`
- [ ] **P8** OS `prefers-reduced-motion` auto-sync into app motion preference on first visit
- [x] **P9** Feedback textarea `resize-y` — `599fcc1`
- [ ] **P10** `apple-touch-icon`, `icon-192`, `icon-512` (PWA installability)
- [ ] **P11** `robots.txt` / `sitemap.xml` in `public/`
- [ ] **P12** Page transitions (120ms cross-fade between routes)
- [ ] **P13** Cookie / analytics consent banner (if telemetry on in prod)
- [ ] **P14** Loading spinner brand-color fallback verification
- [ ] **P15** `WhimsySprite` motion-reduced test coverage

---

## Notes / Conventions
- Per repo policy, every `backend/` change needs a spec update in `specifications/backend-design.MD` first + TDD.
- Frontend changes: keep tests green (`npx vitest run`, `npx eslint .`, `npx tsc --noEmit`).
- Commit-often. Reference the item ID (e.g., `H1`, `M5`) in the commit body.
- Delete this file before final cleanup once all items are addressed (or marked won't-do with a note).
