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
- [ ] **P0b — Turnstile 400/401 surface as generic "Something went
      wrong" toast.** 24h Log Analytics shows two distinct failure
      modes both falling through `apiService.ts`'s generic 4xx branch
      (line 396): `_validate_turnstile_token` (HTTP 400, missing/empty
      token, ~21 events at 6-7ms) AND Cloudflare `success: false` with
      `["invalid-input-response"]` (HTTP 401). Neither has a specific
      FE mapping. **Pending:** add `turnstile_failed` code in
      `apiService.ts` for 401 (and 400 on quiz endpoints), trigger
      `resetTurnstile()` auto-retry from `LandingPage.submitCategory`,
      consider clearing stale `turnstileToken` on page-mount.
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
- [ ] **1.** Header divider line under "Quafel — The personality quiz
      for everything" → make transparent (no stroke).
- [ ] **2.** Idle WhimsySprite renders as two stationary balls, one 50%
      smaller and 50% transparent than the other (same shape/color as
      the active spinner). Only spin while the system is thinking.
- [ ] **3.** Move the input box up so it sits just beneath "A
      personality quiz for any subject".
- [ ] **4.** "Enter any topic to start your quiz" → same color as "A
      personality quiz for any subject", italic, smaller font size.
- [ ] **5.** Change tagline "A personality quiz for any subject" → "A
      personality quiz for… everything."
- [ ] **6.** "Popular" / "Random" chip rows need top margin so the
      hover-grown chips don't intersect the text-input box.
- [ ] **7.** Move "Popular" closer to the "Create my quiz" button.
- [ ] **8.** Desktop: widen the suggested-topic container so 3-per-row
      is the common layout (currently 2).
- [ ] **9.** Agent over-confidently invents content for well-known
      topics (e.g. "Which Hunger Games district am I?" → should yield
      the 13 canonical districts). Prompt audit + per-topic grounding.

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
