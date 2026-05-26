# UX feedback batch — 2026-05-25

Tracking the 9 findings from the May 25 user review. P0 = "Something
went wrong" reproduction blocks all other work because we cannot trust
the deploy without root-causing it first.

## Status

- [x] **P0a — redis.save_state.fail (ValidationError extra_forbidden)**
      → fixed in `backend/app/services/redis_cache.py`
      `_normalize_graph_state_for_storage`. The agent's `GraphState`
      TypedDict carries transient working keys (`analysis`,
      `topic_knowledge`, tool scratchpads) that are not part of the
      canonical `AgentGraphStateModel` schema (which uses
      `extra='forbid'`). Filter to allowed fields before validation, and
      migrate any legacy `analysis` payload to `topic_analysis` so we
      don't drop the planner's normalization decision. Regression test
      `test_normalize_graph_state_drops_legacy_analysis_and_unknown_keys`
      added; full 1078-test unit suite green.
- [x] **P0b — Turnstile 400/401 surface as generic "Something went
      wrong" toast.** Fixed in `frontend/src/services/apiService.ts`
      (new `turnstile_failed` mapping for 401 and 400-with-Turnstile-detail,
      friendly retriable message) + `frontend/src/pages/LandingPage.tsx`
      (queue + transparent auto-retry once a fresh token arrives from
      the invisible widget via `pendingTurnstileRetryRef`).
- [ ] **P0c — smoke gap.** `backend/scripts/prod_render_smoke.py` and
      `backend/scripts/prod_precompute_smoke.py` never answer a
      question, so they don't exercise `save_quiz_state`'s write-path
      and missed the P0a regression. Pending: add an "answer one
      question" step.
- [ ] **P1 — `final_profile_writer` StructuredOutputError.** Trace
      `2f92ea65-baa4-4eaf-8c42-be00298acb24`. `gemini/gemini-flash-latest`
      returns text that the Responses-API parser can't extract as
      structured output → user can't finish quiz at FINISH_NOW. Pending:
      inspect `_do_structured_response` for Gemini output[0] fallback.
- [x] **1.** Header divider transparent — `Header.tsx` `border-b
      border-border/40` removed.
- [x] **2.** WhimsySprite split into idle (two stationary balls: full +
      50%-smaller / 50%-opacity) and `spinning` (existing SuperBalls).
      All three LandingPage render branches (subtitle / preparing /
      submitting) updated; preparing + submitting pass `spinning`,
      subtitle stays idle. Tests rewritten + 2 new idle-state assertions.
- [x] **3.** Input box moved up — new `.lp-space-sub-form-tight` CSS
      utility replaces `.lp-space-sub-form` (1.25rem on mobile, 1.5rem
      tablet, 2rem desktop) so the form sits just beneath the tagline.
- [x] **4.** Hint text "Enter any topic to start your quiz" restyled to
      `text-xs italic text-muted/90` (matches subtitle colour, italic,
      smaller).
- [x] **5.** Tagline copy → "A personality quiz for… everything." in
      `defaultAppConfig.ts`, `LandingPage.tsx` fallback, and
      `backend/appconfig.local.yaml` content.landingPage.subtitle.
- [x] **6.** Chip rows top clearance — `.lp-topic-explorer` padding-top
      bumped to 0.75rem + `.lp-topic-chip-cloud` gets 0.25rem inset top
      so hover-scaled chips don't visually intersect the input.
- [x] **7.** "Popular" moved closer to the "Create my quiz" button —
      `TopicSuggestionExplorer` outer `mt-8` → `mt-3`.
- [x] **8.** Desktop topic container widened — `LandingPage` form
      wrapper gets `lg:max-w-3xl` so the chip cloud has room for the
      3-per-row layout at ≥1024px without disturbing the mobile/tablet
      `lp-form-maxw` default (36rem).
- [ ] **9.** Agent over-confidently invents content for well-known
      topics. Pending: prompt audit + canonical-set grounding (see P1
      section below).

## Findings

### P0a — redis.save_state.fail (RESOLVED)

**Symptom (prod logs, 24h):** repeated
`redis.save_state.fail` errors with
`ValidationError(extra_forbidden, loc=('analysis',))` from
`AgentGraphStateModel.model_validate`. State persistence silently
failed mid-quiz, so subsequent `/quiz/answer` calls 404'd on the
session, surfacing as the generic "Something went wrong" toast.

**Root cause:** `backend/app/agent/graph.py` nodes write the legacy
key `"analysis"` (lines 235/293/360/670/733/875) — this is intentional
working state for the precompute-short-circuit convention (see
`api/endpoints/quiz.py:807`, `:454`, `:727`). The canonical schema
field is `topic_analysis` (set by `graph.py:515`). Both keys can be
live simultaneously in a single `GraphState` dict. The Pydantic
schema has `extra='forbid'` (enforced by
`test_state_consistency.py` and `test_state_roundtrip.py`), so
`save_quiz_state` rejected every state containing the working
`"analysis"` key.

**Fix:** Strip-and-migrate in the normalize layer rather than
relaxing the schema. `_normalize_graph_state_for_storage` now:
1. If `analysis` is set and `topic_analysis` is empty, copy
   `analysis → topic_analysis` (don't lose the planner's decision).
2. Filter the dict to keys present in
   `AgentGraphStateModel.model_fields` before validation.

**Why not change the schema:** existing tests explicitly assert
`extra='forbid'` to catch typo'd field names from agent nodes. The
strip+migrate approach preserves that defensive guarantee while
absorbing the legitimate TypedDict scratchpad keys.

### P0b — Turnstile 400 + 401 fall through generic mapping (PENDING)

**Symptom:** Users see "Something went wrong with your request.
Please refresh and try again." (`apiService.ts:396` fallback for
unmapped 4xx). 24h logs show:
- ~21× HTTP 400 on `/api/v1/quiz/start`, 6-7ms duration → matches
  `_validate_turnstile_token` raising for missing/empty/non-string
  token (`backend/app/api/dependencies.py:185-197`).
- 14+ `Turnstile verification failed` warnings with
  `error_codes: ["invalid-input-response"]` → HTTP 401 from
  `verify_turnstile` (`dependencies.py:273`).

**Suspected mechanism (P0b 401s):** stale token reuse. `LandingPage`
keeps `turnstileToken` in component state across `submitCategory`
calls; if the user back-navigates to the landing page after a
successful start, the stored token is already consumed
server-side. Next submit sends the consumed token → Cloudflare
returns `invalid-input-response`.

**Suspected mechanism (P0b 400s):** less clear. `LandingPage.submitCategory`
gates on `!turnstileToken` (line 67), so submit shouldn't fire
without one. Possibilities: extension/ad-blocker strips the
Turnstile script, or `TopicSuggestionExplorer` calls `submitCategory`
before the invisible widget has produced its first token.

**Recommended fix (next session):**
1. `apiService.ts`: map 401 → `{code: 'turnstile_failed', message:
   "Security check needed a refresh — please try again.", retriable:
   true}`. Also map 400 with detail containing "Turnstile" similarly.
2. `LandingPage.tsx`: on `turnstile_failed`, call `resetTurnstile()`
   then auto-retry the submit once (the widget re-executes
   invisibly).
3. Optional: clear `turnstileToken` in a `useEffect` cleanup so
   stale tokens never persist across mount/unmount cycles.

### P0c — smoke gap (PENDING)

Both prod smoke scripts walk the precompute happy-path (start →
synopsis → characters) but never POST an `/answer`. The
`save_quiz_state` write-path is only exercised once a real adaptive
turn happens. Add a "answer Q1" step to
`prod_render_smoke.py` so any future schema/normalize bug breaks CI
deploy gate.

### P1 — Gemini structured-output (PENDING)

Trace `2f92ea65-baa4-4eaf-8c42-be00298acb24`. Tool
`final_profile_writer`, model `gemini/gemini-flash-latest`. The raw
Responses-API output contains valid JSON in `output[0].text` but
`_do_structured_response` (`llm_service.py:863`) can't extract it.
Likely needs a fallback path: when no tool_calls present but
`output[0]` is `ResponseOutputText`, parse `.text` as JSON.
