# Quizzical / Quafel — Static & Content Subpages Review

**Date:** 2026-06-29
**Reviewer:** Content/accuracy audit (authoring-only pass)
**Scope:** Static / content / sub pages and footer/nav links in `frontend/src`, cross-checked against actual backend behavior (no-auth flow, Turnstile, LangGraph agent, FAL image generation, Postgres/Redis persistence, the new first-party analytics `POST /api/v1/events`, and per-result OG/share meta).

> **Note on file paths:** the live app lives in the nested `quizzical/quizzical/` directory. All paths below are relative to that project root (e.g. `backend/appconfig.local.yaml`, `frontend/src/...`).

---

## 1. Executive summary

The four user-facing content pages (About, Terms, Privacy, Donate) are **not** hard-coded in React — they are thin `StaticPage` wrappers that render Markdown pulled from backend config at `backend/appconfig.local.yaml` → `quizzical.frontend.content.<pageKey>`. The page components themselves are fine; **every content issue is a copy/data-claim issue in that YAML.**

The single most serious problem is the **Privacy Policy**, which is materially inaccurate and now actively misleading given features that shipped after it was written (Jan 2025 dated):

- **It claims no persistent storage of quiz data.** In reality the backend persists the **full quiz session — category/topic, complete Q&A transcript, chosen character set, the final result, and any user feedback text — to Postgres `session_history` indefinitely** (no TTL, no purge job). This is a direct, demonstrable contradiction.
- **It says "No third-party analytics scripts are loaded" but is silent on the new first-party analytics** (`POST /api/v1/events`: `quiz_start` / `quiz_complete` / `share_click`). The statement is technically true (the funnel is first-party and DNT-respecting) but the policy never discloses the funnel at all, which a privacy policy must.
- **It lists only "an AI provider" (the LLM) as a third party.** The app actually sends data to **multiple LLM providers (Google Gemini + OpenAI), an image-generation provider (FAL / fal.ai), and Cloudflare Turnstile**, and loads **Google Fonts** + the **Cloudflare Turnstile script** from third-party origins on every page.
- **It mis-describes localStorage** ("remember UI preferences e.g. dark mode"). There is no dark-mode toggle; the only `localStorage` key is a reduced-motion override flag, and quiz state lives in `sessionStorage`.
- **It references a "contact form" that does not exist** anywhere in the app.

The **Terms** page is mostly serviceable but stale-dated and references a nonexistent contact path. **About** has minor inaccuracies (privacy claim echoes the bad Privacy copy; references a contact channel that doesn't exist). **Donate** ships a literal **placeholder link** (`github.com/sponsors` with the visible text "← replace with your link") — a launch blocker.

No `lorem ipsum`/`TODO` strings, but the Donate placeholder and the "Last updated: January 2025" dates qualify as unfinished/outdated content.

---

## 2. Pages found (route → source)

### Content/static pages (Markdown from backend config)
| Page | Route | Renderer component | Content source (config key) |
|---|---|---|---|
| About | `/about` | `frontend/src/pages/AboutPage.tsx` → `StaticPage` | `content.aboutPage.body` |
| Terms | `/terms` | `frontend/src/pages/TermsPage.tsx` → `StaticPage` | `content.termsPage.body` |
| Privacy | `/privacy` | `frontend/src/pages/PrivacyPage.tsx` → `StaticPage` | `content.privacyPolicyPage.body` |
| Donate | `/donate` | `frontend/src/pages/DonatePage.tsx` → `StaticPage` | `content.donatePage.body` |

- Shared renderer: `frontend/src/pages/StaticPage.tsx` (renders `body` as Markdown via react-markdown + GFM; falls back to typed `blocks`; graceful "Content Not Available" if the key is missing).
- Routing: `frontend/src/router/AppRouter.tsx` (lines 126–151). Document titles set in `DocumentTitleUpdater` (lines 89–110).

### Other pages / chrome
| Page | Route | Source file | Notes |
|---|---|---|---|
| Landing | `/` | `frontend/src/pages/LandingPage.tsx` | Topic input + examples (`content.landingPage`). |
| Quiz flow | `/quiz` | `frontend/src/pages/QuizFlowPage.tsx` | Guarded by `RequireQuiz` (needs a quizId). |
| Result | `/result`, `/result/:resultId` | `frontend/src/pages/FinalPage.tsx` | Shareable result; has SocialShareBar. |
| 404 | `*` | `frontend/src/pages/NotFoundPage.tsx` | Content from `content.notFoundPage` w/ hard-coded fallbacks. |
| Error (component, not a route) | n/a | `frontend/src/pages/ErrorPage.tsx` | Used as error UI; English defaults baked in. |
| Result preview (DEV only) | `/dev/result` | `frontend/src/dev/ResultPreview` | Stripped from prod build. |

### Footer / header nav links
- **Footer** (`frontend/src/components/layout/Footer.tsx`): links from `content.footer` → **About `/about`, Terms `/terms`, Privacy `/privacy`, Donate `/donate`** + copyright. No external links, no Contact link, no GitHub link.
- **Header** (`frontend/src/components/layout/Header.tsx`): wordmark only ("Quafel — The Personality Quiz for Everything"), links to `/`. No nav menu.
- Per-result share targets (X, Facebook, LinkedIn, WhatsApp, Reddit, Email, copy-link, native share): `frontend/src/components/result/SocialShareBar.tsx`.
- Per-result crawler OG/meta SSR doc: `GET /api/v1/result-meta/{id}` in `backend/app/api/endpoints/results.py` (OpenGraph + Twitter card; human bounce to SPA).

---

## 3. Ground-truth: what the app actually does today

Cross-checked against the backend so the policy text can be corrected against reality:

- **No accounts / anonymous.** No auth, no login. Confirmed — no user identity is collected.
- **Quiz generation** = LangGraph agent (`feature_flags.flow_mode: agent`) calling **Google Gemini** (`gemini/gemini-2.5-flash`, `gemini-flash-latest`) **and OpenAI** (`gpt-4o-mini` for several tools: `profile_writer`, `question_generator`, `next_question_generator`, `decision_maker`). The user's topic and answers are sent to these providers. Source: `backend/appconfig.local.yaml` → `llm.tools.*`.
- **Image generation** = **FAL (fal.ai)** pipeline. Source: `backend/app/services/image_pipeline.py`, `image_service.py`; `media_assets.storage_provider` defaults to `'fal'` (`backend/app/models/db.py`).
- **Cloudflare Turnstile**: bot protection on `/quiz/start`. The Turnstile JS is loaded on every page (`frontend/index.html` line 46–49). Server verification in `backend/app/api/dependencies.py:verify_turnstile` posts the token (+ caller IP as `remoteip`) to `challenges.cloudflare.com`. **Currently disabled** in the shipped config (`features.turnstileEnabled: false`, empty `turnstileSiteKey`; backend `ENABLE_TURNSTILE` off / local bypass).
- **Persistence (THE key fact for Privacy):**
  - **Postgres `session_history`** stores, per quiz: `category` (the topic), `category_synopsis`, `session_transcript`, `qa_history`, `character_set`, `final_result`, `user_sentiment` + `user_feedback_text`, completion flags, timestamps. Written during `/quiz/start` and updated through the flow (`backend/app/api/endpoints/quiz.py` `_persist_*` helpers; `feedback.py` persists ratings + free-text feedback). **There is no retention window, TTL, or purge job on this table** — rows persist indefinitely. (`db/init/*.sql` has only FK `ON DELETE CASCADE`s and a `media_assets.expires_at` used for FAL image rehosting, not user-data retention.)
  - **Postgres `characters` / `media_assets`** store generated profiles and image blobs/URLs long-term (reused across sessions).
  - **`content_flags`** store a **hashed client IP** (`client_ip_hash`) for abuse handling.
  - **Redis** stores transient quiz state with a **1-hour TTL** (`save_quiz_state(..., ttl_seconds=3600)`).
  - **Client storage:** `sessionStorage` for the active quiz id/state (1-hour soft timeout; cleared on tab close) — `frontend/src/utils/session.ts`. `localStorage` is used **only** for a `__ALLOW_MOTION__` reduced-motion override (`frontend/index.html`). **No app cookies are set.** (Cloudflare Turnstile may set its own cookie when enabled.)
- **First-party analytics (NEW):** `frontend/src/services/analytics.ts` `track()` → `POST /api/v1/events`. Events: `quiz_start` (fired in `store/quizStore.ts:207`), `quiz_complete` (`quizStore.ts:294`), `share_click` (`SocialShareBar.tsx`, `method` = channel). It is **first-party / vendor-free** (emits a structured log line; no DB table, no third-party SDK), **respects Do-Not-Track** (no-ops on `navigator.doNotTrack === '1'`), and **props are allow-listed** server-side (`method`/`source`/`variant` only; everything else dropped; no PII; client IP intentionally not logged). Source: `backend/app/api/endpoints/events.py`.
- **Per-result OG/share meta (NEW):** `GET /result-meta/{id}` renders per-result OpenGraph/Twitter tags (title/description/image from the stored result) for social crawlers. `frontend/index.html` carries generic site-level OG fallbacks.

---

## 4. Issues, by page

Severity: **P0** = launch blocker / legally material inaccuracy · **P1** = important inaccuracy/gap · **P2** = polish.

### 4.1 Privacy Policy (`content.privacyPolicyPage.body`) — **multiple P0**

| # | Severity | Claim in current copy | Reality | 
|---|---|---|---|
| PR-1 | **P0** | "Quiz answers … held in memory for result generation only"; "Session data is discarded when your browser session ends. **No quiz answers are written to persistent storage on our servers.**" | **False.** Topic, full transcript, answers, character set, final result, and feedback text are written to Postgres `session_history` and retained **indefinitely**. |
| PR-2 | **P0** | "Quiz input … processed in real-time; **not stored persistently**." | **False** — `category` is stored in `session_history`. |
| PR-3 | **P0** | "No third-party analytics scripts are loaded." (and no mention of any analytics at all) | A **first-party** funnel (`/api/v1/events`) now exists and must be disclosed. The "no third-party scripts" line is true but the policy omits the first-party funnel entirely. |
| PR-4 | **P0** | "AI provider — Quiz generation relies on **an** external large-language-model API." | Understated. Data goes to **two LLM providers (Google Gemini + OpenAI)** **and** an **image provider (FAL/fal.ai)**. Image provider not mentioned at all. |
| PR-5 | **P1** | "No tracking cookies are set. … `localStorage` solely to remember UI preferences (e.g., dark mode)." | No dark mode exists; `localStorage` only holds a **reduced-motion** flag; quiz state is in `sessionStorage`. Also should note Cloudflare Turnstile may set its own cookies when enabled, and Google Fonts is loaded from a third-party origin. |
| PR-6 | **P1** | "We do not store personal data, there is nothing to request deletion of." | Misleading given indefinite session storage + hashed-IP abuse flags. Retention + a contact route for requests should be stated. |
| PR-7 | **P1** | "unless you voluntarily provide them (e.g., via **a contact form**)." | No contact form exists. |
| PR-8 | **P2** | "*Last updated: January 2025*" | Stale; must be bumped when republished. |

### 4.2 Terms of Service (`content.termsPage.body`) — **P1/P2**

| # | Severity | Issue |
|---|---|---|
| TM-1 | **P1** | §8 Contact: "open an issue on our GitHub repository." There is no GitHub link anywhere in the app, and the repo may not be public. Either add a working contact channel or soften. |
| TM-2 | **P2** | "*Last updated: January 2025*" — stale date. |
| TM-3 | **P2** | §4 mentions "logo" and "original interface design" as owned IP — fine, but there is no logo asset wired beyond the wordmark; low-risk, leave unless desired. |

### 4.3 About (`content.aboutPage.body`) — **P1/P2**

| # | Severity | Issue |
|---|---|---|
| AB-1 | **P1** | "Privacy First … We don't store your answers beyond the session." — same false claim as PR-1; must be corrected to match reality. |
| AB-2 | **P2** | "Get in Touch … Reach out via the links in the footer" — the footer has **no contact link** (only About/Terms/Privacy/Donate). Either add a contact method or reword. |
| AB-3 | **P2** | "copy your result link and share with friends" — accurate, but the app now has a full share tray (X/FB/LinkedIn/WhatsApp/Reddit/Email/native); could be upgraded to mention it. Optional. |

### 4.4 Donate (`content.donatePage.body`) — **P0/P2**

| # | Severity | Issue |
|---|---|---|
| DN-1 | **P0** | Ships a **literal placeholder**: `> **[Donate via GitHub Sponsors](https://github.com/sponsors)** ← replace with your link`. The link is dead and the instruction text is user-visible. Must be replaced with a real link or the section removed before launch. |
| DN-2 | **P2** | "open-source project" / "Star the repository on GitHub" / "Submit a pull request" — only valid if the repo is actually public. If not, these are inaccurate. |

### 4.5 404 / Error / Header / Footer — OK
No inaccuracies. 404 (`NotFoundPage.tsx`) and `ErrorPage.tsx` are generic and correct. Header tagline ("The Personality Quiz for Everything") is consistent with About. Footer links all resolve to real routes.

---

## 5. Proposed corrected content (ready to paste)

These are drop-in replacements for the `body:` values in `backend/appconfig.local.yaml`. Written as plain Markdown in the existing voice. **Bump the "Last updated" date when applied** (shown as 2026-06-29 below). Adjust the bracketed `[contact …]` once a real contact channel is chosen.

> Assumption to confirm before publishing: that the project will offer **a contact email or form**. The drafts below add a generic "contact us at [hello@quafel.app]" placeholder — replace with the real address, or wire a contact route. If no contact channel will exist, replace those lines with "via the project maintainers."

### 5.1 Privacy Policy — replacement `privacyPolicyPage.body`

```markdown
*Last updated: June 2026*

Your privacy matters. This policy explains what data Quafel collects, how it is used, who we share it with, and the choices you have. Quafel does not require an account and never asks for your name, email, or other identifying details to take a quiz.

## Information We Collect

| Category | Details |
|---|---|
| **Quiz topic** | The topic you enter. It is sent to our AI providers to generate your quiz and is stored with your quiz session (see Data Retention). |
| **Quiz answers & result** | The questions, your answers, the generated characters, and your final result are stored with your quiz session so the result link keeps working. |
| **Optional feedback** | If you rate or comment on a result, your rating and any text you submit are stored with that session. |
| **Abuse signals** | A one-way hash of your IP address may be stored if content is flagged, solely to prevent abuse. We do not store your raw IP with your quiz. |
| **Product analytics** | We record a few first-party, non-identifying events (see Analytics below). |

We do **not** collect names, email addresses, or other personally identifiable information.

## Analytics

Quafel uses a lightweight, **first-party** analytics funnel — there is **no third-party analytics SDK, no advertising pixel, and no cross-site tracking**. We record only three coarse events — when a quiz starts, when a quiz completes, and when a result is shared (including the share channel, e.g. "copy" or "x"). These events carry **no personal data and no quiz content**; the event payload is strictly allow-listed on the server and your IP address is not recorded with it.

We honour **Do Not Track**: if your browser sends a Do-Not-Track signal, we send no analytics events at all.

## Cookies & Local Storage

Quafel sets **no cookies** of its own and loads **no tracking cookies**. We use your browser's `sessionStorage` to keep your in-progress quiz working (cleared when you close the tab), and `localStorage` only to remember a reduced-motion display preference. Cloudflare Turnstile (see below) and our font provider may set their own cookies or make their own requests when those features are active.

## Third-Party Services

Your quiz topic and answers are sent to the following providers so we can generate your quiz. Please review each provider's own privacy policy for how they handle data:

- **AI text providers** — quiz questions, characters, and results are generated using large-language-model APIs from **Google (Gemini)** and **OpenAI**.
- **AI image provider** — character and result images are generated using **fal.ai (FAL)**.
- **Cloudflare Turnstile** — used for bot protection on quiz creation. Cloudflare processes a challenge token and, when enabled, your IP address for verification; no quiz content is shared with it.
- **Google Fonts** — web fonts are loaded from Google's CDN, which may receive your IP address as part of serving the fonts.

## Data Retention

Your in-progress quiz state is held in a short-lived server cache (about one hour) and in your browser's `sessionStorage`. Completed quiz sessions — topic, questions, answers, characters, result, and any feedback — are stored in our database so shared result links keep working. We retain this data until it is no longer needed for operating the service. To request deletion of a specific result, contact us (see below) with the result link.

## Your Choices & Rights

- Enable **Do Not Track** in your browser to switch off analytics entirely.
- Because we never collect your name or email, stored quiz sessions are not linked to your identity.
- To request deletion of a specific stored result, contact us at [hello@quafel.app] with the result URL.

## Changes to This Policy

We may revise this policy to reflect changes in law or our practices. The date at the top of this page will always reflect the most recent update.
```

### 5.2 About — corrected sections of `aboutPage.body`

Replace the **"Privacy First"** and **"Get in Touch"** sections (keep the rest as-is):

```markdown
## Privacy First

We don't require an account, and we never ask for your name or email. To keep your shared result link working, we do store your quiz session (topic, answers, and result) — and we use a few first-party, non-identifying analytics events that respect Do Not Track. We don't use third-party tracking or ad pixels. See our [Privacy Policy](/privacy) for the full picture.

## Get in Touch

Found a bug? Have a suggestion? We'd love to hear from you — reach us at [hello@quafel.app], or consider [supporting the project](/donate) to help keep the lights on.
```

(If a real contact channel won't exist, change the Privacy-First link-out as above but reword "Get in Touch" to: "Have feedback? You can share your result and let us know what you think, or consider [supporting the project](/donate).")

### 5.3 Terms — corrected `termsPage.body` (date + §8)

- Change the first line to: `*Last updated: June 2026*`
- Replace **§8 Contact** with:

```markdown
## 8. Contact

Questions about these Terms? Contact us at [hello@quafel.app].
```

(If no contact email will exist, use: "Questions about these Terms? Reach the project maintainers via the contact details on our site.")

### 5.4 Donate — fix the placeholder in `donatePage.body`

Replace the broken placeholder line:

```markdown
> **[Donate via GitHub Sponsors](https://github.com/sponsors)** ← replace with your link
```

with a real link, e.g.:

```markdown
> **[Buy us a coffee](https://YOUR-REAL-DONATE-LINK)** — every bit helps keep Quafel free.
```

If the project is **not** open-source / has no public repo, also remove or reword the "⭐ Star & Share" and "🐛 Contribute Code or Ideas" sections (the GitHub star/PR asks). If it is public, leave them and add the repo URL.

---

## 6. Recommended priority order

1. **DN-1** (P0) — remove the visible "← replace with your link" placeholder from Donate. Trivial, embarrassing if shipped.
2. **PR-1…PR-4** (P0) — rewrite the Privacy Policy data-collection, retention, analytics, and third-party sections to match reality (full draft in §5.1). This is the legally material one.
3. **AB-1** (P1) — fix the matching false privacy claim in About.
4. **PR-5…PR-7, TM-1, AB-2** (P1) — cookies/localStorage wording, deletion/contact, Terms contact, footer contact reference.
5. **PR-8, TM-2** (P2) — date bumps (handled by the §5 drafts).

## 7. Notes / open questions for the owner
- **Contact channel:** several pages reference a "contact form" / "GitHub issue" / "footer links" that don't exist. Decide on one real channel (email or form) and wire it; the drafts use `[hello@quafel.app]` as a stand-in.
- **Open-source status:** Terms §4/§8 and Donate assume a public GitHub repo. Confirm before keeping those references.
- **Turnstile copy:** Privacy now describes Turnstile; it's currently disabled in config (`features.turnstileEnabled: false`). The draft wording ("when enabled") is safe either way, but if Turnstile ships enabled at launch, the "may set its own cookies" caveat is the accurate phrasing.
- These are **content** fixes only; this pass did not modify any source files (authoring-only, as requested).

---

## Skeptical Review — Verification & Corrections (verified against code)

**Date:** 2026-06-29 · **Method:** disprove-first audit of every material claim above against the actual backend/frontend code (file:line cited). Verdict at the end.

### Verdict (read this first)

**The §5.1 Privacy Policy rewrite is SAFE TO APPLY essentially as-is** — every material factual claim it makes is corroborated by the code, and (critically for a privacy policy) it does **not over-claim collection we don't do, nor omit collection we do**. Three small precision fixes are recommended before publishing (none are legal blockers), listed in **"Corrected facts"** below:

1. **Retention wording is accurate but should not imply an automated purge runs.** The review's headline ("no TTL, no purge job") is **CORRECTED with nuance**: a purge *helper* exists (`SessionRepository.purge_older_than`) but **has no caller** — no cron, no script, no scheduled task invokes it. So in practice rows **are** retained indefinitely today, which is exactly what the §5.1 draft says ("retain this data until it is no longer needed"). The draft is safe; just don't add a specific retention period unless a job is actually wired.
2. **The current policy already contains a "Usage data — aggregate, anonymised request counts" line** that the §4 issue table and §5.1 rewrite both silently drop. Server-side request logging does exist, so this disclosure is not false — but the §5.1 "Analytics" section supersedes it cleanly. Acceptable to drop; noted for completeness.
3. **"Do Not Track" is enforced client-side only.** The §5.1 claim ("we send no analytics events at all" under DNT) is **accurate as written** because the *browser* suppresses the send; just be aware the `/events` endpoint itself does not re-check DNT (it can't see the original signal). No change needed.

Everything else in the review is **VERIFIED**. The Donate placeholder (DN-1) and the four Privacy P0s (PR-1…PR-4) are real and correctly characterised.

### Per-claim verification

| Claim (review) | Status | Evidence (file:line) |
|---|---|---|
| Pages are `StaticPage` wrappers rendering Markdown from `backend/appconfig.local.yaml → content.<pageKey>.body` | **VERIFIED** | `appconfig.local.yaml:2110` (aboutPage), `:2161` (termsPage), `:2183` (privacyPolicyPage), `:2205` (donatePage). |
| **PR-1 / PR-2** Postgres persists topic + transcript + answers + character set + final result + feedback to `session_history`; current policy's "not stored persistently / no quiz answers written to persistent storage" is **false** | **VERIFIED** | Schema: `db/init/init.sql:51-91` (`session_history` with `category`, `category_synopsis`, `session_transcript`, `character_set`, `final_result`, `qa_history`, `user_sentiment`, `user_feedback_text`). ORM mirror: `app/models/db.py:175-246`. Writes: `quiz.py:295-326` (`_persist_initial_snapshot` → upsert synopsis+transcript+character_set), `quiz.py:546-555` (`mark_completed` writes `final_result` + `qa_history`), `feedback.py:113-124` → `database.py:317-344` (`save_feedback` writes sentiment + free-text). Current false copy: `appconfig.local.yaml:2188-2189, 2199-2201`. |
| "There is no TTL/retention window/purge job on `session_history` — rows persist indefinitely" | **CORRECTED (nuance)** | A retention helper **exists**: `database.py:298-315` `purge_older_than(days)` (deletes rows by `last_updated_at`). **BUT it has no caller** — grep across `backend/app`, `backend/scripts`, and `infrastructure/` finds it referenced only by its unit test (`tests/unit/services/test_session_retention.py`) and the README/spec. No cron, no `app/scripts/purge_sessions.py` (does not exist), no scheduled task. `scripts/SCHEDULED_TASK_SETUP.md:1-3` schedules only a **nightly pg_dump backup**, not a purge. `init.sql` has **no** retention trigger on `session_history` (only `media_assets.expires_at` for FAL image rehosting + FK `ON DELETE CASCADE`s). **Net effect: indefinite retention in practice — the review's conclusion holds; only "no purge job *exists*" is imprecise (one exists but is dormant).** Also note: the spec (`backend-design.MD:2556`) says the helper should also filter `is_completed=TRUE`; the implementation deletes **all** rows older than N days regardless — irrelevant to the policy, flagged for the owner. |
| Redis holds transient quiz state with a **1-hour TTL** | **VERIFIED** | `redis_cache.py:189` `save_quiz_state(..., ttl_seconds=3600)` → `:206` `self.client.set(key, payload, ex=ttl_seconds)`. Atomic-merge path also 3600s (`:255, :288`). (RAG cache is a separate 86 400s/24h TTL — `:351` — not quiz data.) |
| `content_flags` stores a **hashed client IP** (`client_ip_hash`) for abuse | **VERIFIED** | `db/init/init.sql:358-367` (`content_flags.client_ip_hash TEXT NOT NULL`), ORM `app/models/db.py:545-562`. |
| **PR-3** A first-party analytics funnel (`POST /api/v1/events`) now exists; current policy omits it; "no third-party analytics scripts" is technically true | **VERIFIED** | Endpoint: `app/api/endpoints/events.py:142-184` (`POST /events`, emits one `analytics.event` structlog line, no DB table, no third-party SDK). Allow-listed events `quiz_start`/`quiz_complete`/`share_click` (`events.py:38, 90`). Props server-side allow-list = `method`/`source`/`variant` only, all other keys **silently dropped** (`events.py:50-56, 109-114`), IP intentionally not logged (`events.py:172-183`). Client: `frontend/src/services/analytics.ts:121-168`. Current copy "No third-party analytics scripts are loaded" `appconfig.local.yaml:2194` — true, but funnel undisclosed. |
| Analytics respects **Do Not Track** | **VERIFIED (client-side)** | `analytics.ts:74-85` `isDoNotTrackEnabled()` + `:124-127` early no-op when DNT. Enforced in the browser; the backend endpoint does not re-check DNT (cannot see the signal) — but since the client never sends, the §5.1 claim is accurate. Tests: `analytics.spec.ts:50-71`. |
| **PR-4** Data goes to **two LLM providers (Gemini + OpenAI)** AND an **image provider (FAL)**; current "an external LLM API" (singular) understates and omits images | **VERIFIED** | Gemini: `appconfig.local.yaml:59,69,75,96,104,110,156,162,168,174,180,199` (`gemini/gemini-2.5-flash`, `gemini/gemini-flash-latest`). OpenAI: `:90,123,144,193` (`gpt-4o-mini` for profile_writer, question_generator, next_question_generator, decision_maker). FAL: `image_pipeline.py:134` (`FAL_KEY`), `media_assets.storage_provider` default `'fal'` (`db.py:463-465`, `init.sql:255`). Current understated copy: `appconfig.local.yaml:2195-2197`. |
| Cloudflare Turnstile: script loaded on **every page**; server verify posts token **+ caller IP (`remoteip`)** to `challenges.cloudflare.com`; **currently disabled** (`turnstileEnabled: false`, empty site key) | **VERIFIED** | Script tag (unconditional, every page): `frontend/index.html:46-49`. Verify: `app/api/dependencies.py:221-269` posts `{secret, response, remoteip}` to `https://challenges.cloudflare.com/turnstile/v0/siteverify` (`:255-257, :266`); hard-bypasses to `True` when disabled (`:223`). Config: `appconfig.local.yaml:2313-2315` (`turnstileEnabled: false`, `turnstileSiteKey: ''`). |
| **Google Fonts** loaded from third-party origin on every page | **VERIFIED** | `frontend/index.html:24-28` — preconnect to `fonts.googleapis.com` / `fonts.gstatic.com` (crossorigin) + 3 stylesheet families (Inter, Nunito, Baloo 2). |
| **PR-5** No dark-mode toggle; only `localStorage` key is a **reduced-motion** flag; quiz state in `sessionStorage` | **VERIFIED** | Only `localStorage` use is `__ALLOW_MOTION__` (`frontend/index.html:38-43`); grep of `frontend/src` finds no other `localStorage` writes. Quiz state in `sessionStorage` (`frontend/src/utils/session.ts:5,18,45,71-72`). Note: the app *does* respect OS `prefers-color-scheme` via CSS (`index.html:13-16`), but there is no dark-mode *toggle* and no dark-mode *preference* stored — so "remember UI preferences e.g. dark mode" at `appconfig.local.yaml:2193-2194` is inaccurate. |
| **No app cookies set** (Turnstile may set its own when enabled) | **VERIFIED** | No `Set-Cookie` / cookie-setting in the app; analytics is cookieless (`analytics.ts:7`). Turnstile cookie only when the third-party script is active. |
| **PR-7 / AB-2 / TM-1** "contact form" / footer contact link / GitHub-issue contact don't exist | **VERIFIED** | No contact form anywhere in `frontend/src` (grep `contact form|/contact|ContactForm` → none; only a `mailto:` *share* target in `SocialShareBar.tsx:163-164`). Current copy: privacy "via a contact form" `appconfig.local.yaml:2192`; terms §8 "open an issue on our GitHub repository" `:2181-2182`; about "links in the footer" `:2156-2158`. |
| **PR-6** "we do not store personal data, nothing to delete" is misleading given indefinite session storage + hashed-IP flags | **VERIFIED** | Current copy `appconfig.local.yaml:2201-2202`; contradicted by `session_history` persistence (above) and `content_flags.client_ip_hash`. |
| **PR-8 / TM-2** "Last updated: January 2025" is stale | **VERIFIED** | `appconfig.local.yaml:2186` (privacy), `:2164` (terms). |
| **DN-1** Donate ships literal placeholder `[Donate via GitHub Sponsors](https://github.com/sponsors) ← replace with your link` | **VERIFIED** | `appconfig.local.yaml:2240` — exact text present, user-visible. |
| **AB-1** About echoes the false "don't store your answers beyond the session" privacy claim | **VERIFIED** | `appconfig.local.yaml:2148-2150`. |
| Per-result OG/share meta endpoint `GET /result-meta/{id}` exists (SSR OpenGraph/Twitter) | **VERIFIED** | `app/api/endpoints/results.py:7-8, 129-137, 188`. |
| Funnel events fire from the claimed call sites with `method` prop on share | **VERIFIED** | `quizStore.ts:207` (`quiz_start`), `:294` (`quiz_complete`); `SocialShareBar.tsx:219` (`method: 'copy'`), `:238` (`method: 'native'`), `:453` (`method: key` per channel). |

### Corrected facts for the rewrite (Information-Collected / Retention / Third-Parties)

The §5.1 draft is accurate. Apply with these precision tweaks:

- **Information We Collect** — accurate as drafted. Optionally fold the legacy "Usage data — aggregate, anonymised request counts for performance monitoring" notion into the **Analytics** section (server request logs do exist); the draft's omission is acceptable since the Analytics section now covers operational telemetry.
- **Retention** — keep the draft's **"We retain this data until it is no longer needed for operating the service."** This is the **accurate** statement today (the `purge_older_than` helper is **not scheduled**, so there is no active automated deletion). **Do not** state a specific retention period (e.g. "90 days") unless/until a purge job is actually wired to call `purge_older_than`. If the owner wants a bounded promise, wire a scheduled caller first, then update the policy to match.
- **Third-Parties** — the draft's list is correct and complete: **Google (Gemini) + OpenAI** (text), **fal.ai (FAL)** (images), **Cloudflare Turnstile** (bot check; processes a challenge token and, *when enabled*, the caller IP via `remoteip`), **Google Fonts** (CDN; may receive IP). The "when enabled" hedge on Turnstile is correct — it is `turnstileEnabled: false` in shipped config. No third party is over- or under-stated.

### Not over-claiming check (the high-stakes part)

The §5.1 draft does **not** assert any collection the app doesn't perform:
- It does **not** claim names/emails/accounts (correct — none collected).
- It does **not** claim third-party analytics/ad pixels (correct — funnel is first-party, cookieless).
- It does **not** claim raw IP is stored with quizzes (correct — only a *hash* in `content_flags`, and IP is not logged with analytics events).
- It **does** disclose what is collected: topic, Q&A, characters, result, optional feedback, hashed-IP abuse flags, and the three funnel events. All corroborated above.

Conclusion: applying §5.1 (with the retention-wording caution) brings the Privacy Policy into accurate alignment with the code. DN-1 (Donate placeholder) remains the other launch blocker and is confirmed real.
