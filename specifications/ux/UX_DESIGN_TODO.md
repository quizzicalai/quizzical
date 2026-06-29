# UX Design Update TODO — May 2026

Source: user feedback batch (14 items). Tracking status as each ships.

## Status legend
- [ ] not started
- [~] in progress
- [x] done

## Items

1. [x] **Final profile image fidelity** — profile image at result sometimes doesn't depict the character. Tighten the FAL prompt for the final-result image: concise *and* specific. Files: backend prompt construction for the final character image (likely `backend/app/services/image_pipeline.py` or wherever the character-result image is built).

2. [x] **Share preview broken** — share modal shows final image failing to load and the share components are transparent (no white background, unreadable). Fix z-index / background on the share modal/card. Files: `frontend/src/components/result/Share*.tsx` and related styles.

3. [x] **Share overlay sizing** — dark semi-transparent backdrop should overflow slightly beyond the share box. Currently it only covers space above/below. Fix overlay layout — likely needs `fixed inset-0` instead of constrained sizing.

4. [x] **Feedback button labels & shape** —
   - Rename "Needs work" → "Poor"
   - All feedback buttons must be the same size (predefined width/height, circular or wide oblong), not text-sized.
   Files: `frontend/src/components/result/Feedback*.tsx` (or wherever rating buttons live).

5. [x] **Feedback box needs a Submit button** — currently the comment box has no submit affordance.

6. [x] **AI "thinking" indicator (upper-right)** —
   - While generating: 2-dot loading spinner (current behavior, ensure consistent)
   - When idle/settled: a 2-dot static "sprite" to the LEFT of the text, similar in size/shape to the spinner.
   Files: `frontend/src/components/quiz/ThinkingIndicator.tsx` (or similar).

7. [x] **Thinking text color** — change to medium grey, dark enough to pass accessibility (WCAG AA on white bg). Probably `text-slate-500` or `text-slate-600`.

8. [x] **Append agent confidence to thinking state** — format: `Getting closer (85% confident)`. Surface confidence value from agent state.

9. [x] **Header text update** — change top-left brand from "Quafel" to "Quafel — The Personality Quiz for Everything". Files: `frontend/src/components/layout/Header.tsx`.

10. [x] **Sticky header on scroll** — the Quafel header should be `sticky top-0` (or fixed) so it stays visible when the user scrolls.

11. [x] **Mobile three-dots styling** — the three-dot menu on mobile shouldn't have a visible border. Set stroke to transparent on mobile.

12. [x] **"Getting things ready" loading message** — replace with a friendly rotating message:
    - Top line (large): `Loading...`
    - Smaller rotating sub-text examples:
      - "Quafel lets you discover who you are — Myers-Briggs Type, Hogwarts House, Famous Elephant — Anything!"
      - (add 3–5 friendly variants)
    Should rotate every few seconds. Files: loading screen / `LoadingScreen.tsx` or similar.

13. [x] **Landing tagline** —
    - Change "A personality quiz for any subject" → "You pick the topic, I'll generate the quiz!"
    - Add the same stationary AI 2-dot sprite to the LEFT of the message.
    - Remove italic styling.
    Files: `frontend/src/components/landing/LandingHero.tsx` (or similar).

14. [x] **Reorder landing form** — move "Enter any topic to start your quiz" hint text to BELOW the "Create my quiz" button (currently above the input).

## Sequencing plan
- Group A (header + landing): 9, 10, 13, 14
- Group B (loading + thinking): 6, 7, 8, 12
- Group C (result page polish): 1, 2, 3, 4, 5
- Group D (mobile): 11

## Test coverage added with this batch

Regression tests pinned for every shipped item. **530 FE tests pass, 30 backend image/api tests pass.**

| Item | New / updated tests |
|------|---------------------|
| 1 | `backend/tests/unit/agent/tools/test_image_tools.py` — `test_result_prompt_always_includes_style_anchor_and_negative`, `test_result_prompt_respects_600_char_budget_under_worst_case`, `test_result_prompt_body_uses_name_and_category_only`, updated `test_result_prompt_prefers_matched_character` |
| 2 | `frontend/src/components/result/SocialShareBar.spec.tsx` — `portals the open modal to document.body`, `gives the modal panel and preview card opaque background fallbacks` |
| 3 | `SocialShareBar.spec.tsx` — `renders a full-viewport semi-transparent backdrop behind the share card` |
| 4 | `FeedbackIcons.spec.tsx` — `renders both rating buttons with identical fixed-size circular shape`, updated "Poor" label assertion |
| 5 | `FeedbackIcons.spec.tsx` — `renders a primary Submit button with visible "Submit" label` |
| 6 | Existing `ThinkingIndicator.spec.tsx` already covers idle/thinking states |
| 7 | `QuestionView.spec.tsx` — `renders the progress phrase in medium-grey, non-italic styling` |
| 8 | `QuestionView.spec.tsx` — `appends the agent confidence …`, `normalises a legacy 0-100 confidence value`, `hides the confidence suffix once loading is complete`; `backend/tests/unit/api/test_question_confidence.py` (5 tests for schema + adapter normalisation) |
| 9 | `Header.spec.tsx` — `renders the "Personality Quiz for Everything" tagline next to the wordmark` |
| 10 | `Header.spec.tsx` — `uses sticky positioning so it stays in place when the page scrolls` |
| 11 | `Footer.spec.tsx` — `hides the menu button border and fill on mobile and restores them at sm:` |
| 12 | `LoadingNarration.spec.tsx` — `exposes a non-empty LANDING_PREPARING_LINES pool with a 4s+ schedule`, `rotates LANDING_PREPARING_LINES on schedule` |
| 13 | `LandingPage.spec.tsx` — `renders the WhimsySprite to the left of the subtitle copy`, `falls back to the new "You pick the topic…" tagline` |
| 14 | `LandingPage.spec.tsx` — updated `renders the helper line BELOW the submit button` with DOM-order assertion |

## CI follow-ups (recommended but not implemented this batch)

Resolved during follow-up audit — the originally-listed gaps were
either already covered or have now been shipped:

- [x] **E2E happy-path** — already covered by
      [frontend/tests/e2e/smoke.spec.ts](frontend/tests/e2e/smoke.spec.ts),
      [fullFlowContract.spec.ts](frontend/tests/e2e/fullFlowContract.spec.ts),
      and [resultPageShare.spec.ts](frontend/tests/e2e/resultPageShare.spec.ts).
- [x] **axe-core integration** — already covered for AnswerTile,
      SynopsisView, ResultProfile, HeroCard, QuestionView, LoadingCard,
      SkipLink, Layout shell in
      [frontend/src/__a11y__/](frontend/src/__a11y__/). LandingPage was
      the only remaining gap and is now covered by
      [LandingPage.a11y.spec.tsx](frontend/src/pages/LandingPage.a11y.spec.tsx).
- [x] **FAL request-shape test** — added 3 tests
      (`test_generate_sends_required_fal_request_shape`,
      `test_generate_forwards_negative_prompt_and_seed_when_provided`,
      `test_generate_omits_optional_fields_when_not_provided`) in
      [test_image_service.py](backend/tests/unit/services/test_image_service.py)
      that capture `subscribe_async` call args and pin the request body
      shape (prompt, image_size, num_inference_steps,
      enable_safety_checker, negative_prompt, seed uint32 mask).
- [x] **CSP allowlist for FAL** — already covered by
      [frontend/tests/e2e/cspCompliance.spec.ts](frontend/tests/e2e/cspCompliance.spec.ts)
      AC-FE-CSP-2 (`img-src https:` allows FAL image URLs).
- [x] **Turnstile bypass guard** — already covered by 15+ tests in
      [backend/tests/security/test_turnstile.py](backend/tests/security/test_turnstile.py)
      and [backend/tests/unit/api/test_dependencies.py](backend/tests/unit/api/test_dependencies.py).
- [x] **Linting in CI** — `eslint . --max-warnings=0` runs in
      [.github/workflows/fe-ci.yml](.github/workflows/fe-ci.yml);
      `ruff check app` + `bandit -r app -ll -ii` run in
      [.github/workflows/api-deploy.yml](.github/workflows/api-deploy.yml).

### Genuine residual gaps (recorded for future PRs)

- [x] **Pre-existing React DOM-nesting warning** — `<p>` containing a
      `<div>` (LandingPage subtitle wraps `WhimsySprite` which renders
      `SuperBalls` divs inside a `<span>`). Fixed by switching the
      subtitle wrapper to `<div role="paragraph">` in
      [frontend/src/pages/LandingPage.tsx](frontend/src/pages/LandingPage.tsx)
      — preserves screen-reader semantics while allowing block-level
      web-component children.
- [x] **`tests/` ruff cleanup** — `ruff check tests` is now green
      (480 → 0). Auto-fixed 403 issues (`I001`, `F401`, whitespace),
      manually fixed 4 `E702`s in
      [test_rate_limit.py](backend/tests/unit/security/test_rate_limit.py),
      and added per-file-ignores in
      [backend/pyproject.toml](backend/pyproject.toml) for the
      remaining 67 test-appropriate violations (`F811` fixture
      overrides, `E402` lazy imports for monkey-patching, `C901` long
      AAA flows, `B017`/`B904`/`F841`/`B007`). CI extended:
      [.github/workflows/api-deploy.yml](.github/workflows/api-deploy.yml)
      now runs `ruff check app tests`, and
      [test_quality_gates.py](backend/tests/security/test_quality_gates.py)
      mirrors the same scope locally.
- [ ] **Sticky-header viewport-resize Playwright check** — unit test
      coverage exists in
      [Header.spec.tsx](frontend/src/components/layout/Header.spec.tsx);
      a real-browser sticky-on-scroll assertion would still add value
      but is low-priority.

