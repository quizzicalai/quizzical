# Canonical Set Coverage Audit — 2026-06-30

Branch: `feat/canonical-correctness` · PR: "fix(canonical): add missing marquee
frameworks + alias/noise-strip fixes + coverage audit"

## Why this audit exists

Black-box testing found that a well-known quiz topic — **DISC** — could produce
a **wrong / approximate** outcome set (LLM-generated archetypes) instead of the
canonical four DISC styles. The owner then asked, explicitly: *"are there other
missing topics like this?"* This document answers that and records the fixes
shipped in the accompanying PR.

There are two distinct failure classes behind the DISC symptom:

1. **App-Config drift drops marquee sets.** Several flagship frameworks
   (MBTI, Enneagram, Big Five/OCEAN, Hogwarts Houses) lived **only** in on-disk
   App-Config YAML (`backend/appconfig.local.yaml`), not in the reviewed CODE
   catalog (`backend/app/agent/canonical_catalog.py`). In prod, App-Config can
   drift or be replaced, and any set/alias that exists only there is silently
   dropped — the quiz then falls through to LLM generation and an approximate
   set. The CODE catalog is supposed to be the floor that always holds.

2. **Phrasing near-misses bypass the canonical lookup.** Even when a set is in
   the catalog, real user phrasings ("What is my DISC type", "DISC personality")
   were not being normalized down to the catalog key (`disc`), so the lookup
   missed and the pipeline fell through to LLM generation.

## DISC reproduction (Task 5)

### Where stored quizzes / sessions live

- DB model: `backend/app/models/db.py` — `SessionHistory` (table
  `session_history`). Key columns:
  - `category` (TEXT) — the raw user topic string (e.g. "What is my DISC type").
  - `category_synopsis` (JSONB), `character_set` (JSONB) — the **snapshot of the
    generated/canonical outcome set** (array of objects with `name`,
    `short_description`, `profile_text`, `image_url`).
  - `final_result` (JSONB), `is_completed`, `completed_at`.
- Characters table: `characters` (`name` UNIQUE, `canonical_key`), linked to
  sessions via `character_session_map` (`character_id` ↔ `session_id`).
- Repository: `backend/app/services/database.py` —
  `SessionRepository.upsert_session_after_synopsis(...)` writes the session +
  `character_set` snapshot + character links; `SessionRepository.mark_completed(...)`
  finalizes `final_result`. `ResultService.get_result_by_id(...)` reads a result.
- DDL: `backend/db/init/init.sql` (`session_history` definition).

How to query what a DISC quiz actually produced (Postgres):

```sql
SELECT sh.session_id, sh.category, sh.created_at,
       jsonb_array_elements(sh.character_set)->>'name' AS outcome_name
FROM   session_history sh
WHERE  sh.category ILIKE '%disc%'
ORDER  BY sh.created_at DESC;
```

If `outcome_name` rows for a DISC session are **not**
`Dominance / Influence / Steadiness / Conscientiousness`, that session was built
from an LLM-generated approximation rather than the canonical set.

> Note: this audit could not enumerate live prod rows from the repo (no DB
> dump checked in). The repro below is established analytically against the
> exact pre-fix code paths, which is sufficient to root-cause and prove the fix.

### The phrasing that produced a wrong DISC set

Tracing `analyze_topic()` → `canonical_for()` on the pre-fix code:

| Raw input | `_strip_question_chrome` (pre-fix) | normalized | `canonical_for` (pre-fix) |
|---|---|---|---|
| `DISC` | `DISC` | `Types of DISC` | ✅ DISC styles |
| `DISC personality` | `DISC personality` | `Types of DISC personality` | ✅ DISC styles (trailing-tool strip caught "personality") |
| **`What is my DISC type`** | **`is my DISC type`** | **`Types of is my DISC type`** | **❌ MISS → LLM approximation** |

Root cause: `intent_classification._strip_question_chrome`'s leading-frame regex
(`_QUESTION_PREFIX_RE`) only removed the single interrogative word ("What"),
leaving `is my DISC type`. The trailing-fit regex only matches `am i` / `are you`
at the **end**, so "is my" was never removed. The leftover tokens
("is", "my", "type") prevented the normalized key from collapsing to `disc`, so
the canonical lookup missed and the pipeline generated approximate archetypes.

This is the most likely culprit class for the observed wrong DISC set: a
near-miss interrogative phrasing. (A second, independent way to get a wrong set
is a **pre-catalog stored pack** — a session persisted before DISC was in the
catalog at all; those rows are historical and only re-generation would correct
them. See "Next steps" re: a persist-time gate / repair pass.)

### Fix (this PR)

- `intent_classification._strip_question_chrome` now also consumes an optional
  copula/aux + subject pronoun + **possessive** after the interrogative word, so
  `What is my DISC type` → `DISC type` (then canonical normalization → `disc`).
- `canonical_sets._strip_noise` broadened symmetrically (see Task 3), so even
  without the chrome strip, `What is my DISC type` resolves canonically.

Verified end-to-end: `analyze_topic("What is my DISC type")` →
`canonical_for(...)` → `[Dominance, Influence, Steadiness, Conscientiousness]`.

## Fixes shipped in this PR

### Task 1 — marquee frameworks added to the CODE catalog

Added to `PASS_1_PERSONALITY_FRAMEWORKS` (and `DND Alignments` to PASS_2) with
exact reviewed sets + aliases, so App-Config drift can no longer drop them:

| Set | Members | Aliases (added) | `outcome_mode` |
|---|---|---|---|
| Myers-Briggs Personality Types | 16 (INTJ…ESFP) | mbti, mbti types, myers-briggs, myers briggs, 16 personalities, sixteen personalities | single |
| Enneagram Types | 9 (Type 1 The Reformer … Type 9 The Peacemaker) | enneagram, enneagram types, 9 enneagram types | single |
| Big Five Personality Traits | 5 (Openness, Conscientiousness, Extraversion, Agreeableness, Neuroticism) | big five, big 5, ffm, five factor, five factor model, ocean, ocean traits | **blended** |
| Hogwarts Houses | 4 (Gryffindor, Slytherin, Ravenclaw, Hufflepuff) | hogwarts house, hogwarts houses, which hogwarts house, harry potter houses, hp houses | single |
| DISC Styles (existing; mode added) | 4 (Dominance, Influence, Steadiness, Conscientiousness) | disc, disc profiles, disc styles | **blended** |
| DND Alignments (new) | 9 (Lawful Good … Chaotic Evil) | dnd alignments, d&d alignments, alignment chart, alignment grid, nine alignments | single |

**`outcome_mode` marker (foundation only).** Every catalog entry now carries a
factual `outcome_mode` field defaulting to `"single"`. Only **DISC** (a profile
blended across D/I/S/C) and **Big Five / OCEAN** (scores across all five traits)
are `"blended"`. Everything else — including MBTI (composited from four
dichotomies but resolving to exactly one of 16 named types), Enneagram, Hogwarts,
love languages, zodiac, temperaments, attachment styles, RIASEC — is `"single"`.
This is recorded for a **future** blended-outcome feature. **No** blended
generation/UI is built here; current single-character behavior is byte-identical.

The same App-Config drift also affected **aliases**: the YAML alias list for a
title used to *replace* the code list, which is why `big 5` / `ffm` had silently
disappeared from Big Five. `_merge_config` now **unions** alias lists per title
(code aliases first, then App-Config additions), making the code catalog a true
floor for aliases too.

### Task 2 — OCEAN alias-vs-title collision fixed

`canonical_sets._add_index_key` was first-write-wins, and direct titles index
before aliases. The geographic title **"Oceans"** derives the singular variant
`ocean`, which claimed the key before Big Five's `ocean` alias could register —
so `canonical_for("OCEAN")` returned the five oceans, not the Big Five.

Fix: index keys now carry a **provenance/precedence** rank — lowest to highest:
`alias_variant` < `title_variant` < `alias` < `exact_title`. An explicit alias
may overwrite a *derived* title variant but **not** an exact title; an
alias-*derived* variant is the weakest and must never overwrite another set's
title-derived variant (this was a follow-up review finding — without it, e.g.
the "Musical Modes (7)" alias variant "church mode" stole the "Church Modes"
title variant, and "classical element" re-routed from the 4-element to the
5-element/Aether set).

A short acronym alias (a single short token whose letters are the initials of
the set's members, e.g. `OCEAN` = Openness/Conscientiousness/Extraversion/
Agreeableness/Neuroticism) is handled in a **separate, CASE-SENSITIVE acronym
map** consulted only when the user typed it uppercase. It is diverted out of the
case-blind index **only when it collides** with another set (so non-clashing
acronyms like `riasec`/`vark` still resolve in lowercase). Result, proven by
unit tests:

- `canonical_for("OCEAN")` → Big Five (uppercase acronym).
- `canonical_for("ocean")` / `canonical_for("Ocean")` → geographic Oceans
  (lowercase/title-case is the body of water, never Big Five).
- `canonical_for("oceans")` → geographic Oceans (the exact title is untouched).
- `canonical_for("riasec")` / `canonical_for("vark")` → Holland Codes / VARK
  (non-colliding acronyms keep working in any case).

Lookup itself follows a **full-original-first, strip-on-miss** contract: the
un-stripped string is tried before any noise stripping, the stripped form is a
lookup key only (never substituted for the user's topic downstream), and a strip
that reduces a topic to empty/too-short falls back to the original.

### Task 3 — broadened noise stripping for real phrasings

`canonical_sets._strip_noise` + `_LEADING_QFRAME_RE`, and
`intent_classification._strip_question_chrome`:

- Strip trailing descriptors: ` personality`, ` personality type(s)`,
  ` style(s)`, ` result(s)` (` test/quiz/assessment` were already handled).
- Strip leading `my `/`your ` (and other possessives) for bare phrasings.
- Fixed the leading Q-frame regex to consume `is my` / `are your` etc., so
  `What is my DISC type` → `DISC` instead of the old mangled `is my DISC type`.

Table-driven tests cover: `DISC personality`, `What is my DISC type`,
`Big Five personality`, `my love language`, `which hogwarts house am I`.

## Task 4 — coverage of well-known / popular quiz topics

`canonical_for` resolution checked against the merged (code + App-Config)
catalog. "Code?" = also present in the drift-proof CODE catalog after this PR.

| Topic | Expected size | Present? | Code? | Recommendation |
|---|---|---|---|---|
| MBTI | 16 | ✅ | ✅ (this PR) | done |
| Enneagram | 9 | ✅ | ✅ (this PR) | done |
| Big Five / OCEAN | 5 | ✅ | ✅ (this PR) | done |
| Hogwarts Houses | 4 | ✅ | ✅ (this PR) | done |
| DISC | 4 | ✅ | ✅ (already) | done; mode → blended |
| D&D Alignment grid | 9 | ✅ | ✅ (this PR) | done |
| Love Languages | 5 | ✅ | ✅ (already) | none |
| Western Zodiac signs | 12 | ✅ | ✅ (already) | none |
| Chinese Zodiac | 12 | ✅ | ✅ (already) | none |
| Four Temperaments | 4 | ✅ | ✅ (already) | none |
| Attachment Styles | 4 | ✅ | ✅ (already) | none |
| RIASEC / Holland Codes | 6 | ✅ | ✅ (already) | none |
| Learning Styles (VARK) | 4 | ✅ | ✅ (already) | none |
| Tarot Major Arcana | 22 | ✅ | ✅ (already) | none |
| Seven Deadly Sins | 7 | ✅ | App-Config only | recommend ADD to code |
| Chakras | 7 | ✅ | App-Config only | recommend ADD to code |
| Wu Xing / Doshas / Classical Elements | 5/3/4–5 | ✅ | App-Config only | recommend ADD to code |
| **Greek gods (Olympians)** | 12 | ❌ | ❌ | **recommend ADD** (see set below) |
| **Generations** | 6–7 | ❌ | ❌ | **recommend ADD** (see set below) |
| **Marvel alignment / Star Wars alignment** | — | ❌ | ❌ | **skip** — not a closed canonical taxonomy; these are open character/faction lists best left to the media-character path, not the bounded canonical catalog |

### Missing high-value sets — exact recommended membership

These are clearly correct and bounded; recommend adding to the CODE catalog in a
follow-up (kept out of this PR to keep it tightly scoped to the DISC class of
correctness bugs):

- **Twelve Olympians** (Greek gods):
  `Zeus, Hera, Poseidon, Demeter, Athena, Apollo, Artemis, Ares, Aphrodite,
  Hephaestus, Hermes, Dionysus`
  (aliases: greek gods, olympian gods, twelve olympians). Note: Hestia/Hades are
  sometimes swapped for Dionysus/Ares — pick the standard 12 above and document.
- **Generations** (Western/Pew):
  `Lost Generation, Greatest Generation, Silent Generation, Baby Boomers,
  Generation X, Millennials, Generation Z, Generation Alpha`
  (aliases: generations, age generations). Bounded but version-sensitive at the
  tail (Gen Alpha boundary) — document the source.

### Drift-risk inventory (App-Config-only sets — same bug class as the marquee frameworks)

The following are currently resolvable **only** because App-Config YAML supplies
them; they are NOT in the reviewed CODE catalog and would vanish on App-Config
drift. Recommend migrating the high-value ones into the code catalog in a
follow-up PR (low risk, declarative):

Ayurvedic Doshas; Chakras (Seven); Classical Elements (Greek, 4 & 5);
Classical Planets (7); D&D Ability Scores; D&D Alignments *(added this PR)*;
Enneagram Types (1–9) *(now also in code under "Enneagram Types")*;
Godai; Greek Muses (9); Major Arcana (Tarot); Months of the Year;
Moon Phases (8); Musical Modes (7); Platonic Solids; Primary Tastes (Five);
Quadrivium / Trivium; Rainbow Colors (ROYGBIV); Roman Numerals; SI Base Units;
Seasons (Four); Seven Deadly Sins; Seven Heavenly Virtues;
Seven Wonders of the Ancient World; Solar System Planets (IAU 8);
Tarot Suits; Wu Xing; Cardinal & Intercardinal Directions (8);
Chess Pieces/Files/Ranks; Chromatic Pitch Classes (12);
HTTP Status Classes; Latin Alphabet; Days of the Week; Solfège (Diatonic).

(Many overlap conceptually with code-catalog sets under different titles — e.g.
code has "Western Zodiac Signs"/"Solar System Planets"/"Tarot Major Arcana
Cards" — so the practical user-facing risk is concentrated in the genuinely
unique ones: Seven Deadly Sins, Chakras, Wu Xing, Doshas, Classical Elements,
Seven Heavenly Virtues, Platonic Solids.)

## Next steps (explicitly deferred — pending owner decision)

Per the owner's instruction, the following were **not** implemented here and are
the recommended follow-on, pending a reject-vs-auto-repair decision:

1. **Persist-time canonical gate (nightly or on write).** Before a generated
   quiz's `character_set` is persisted, compare it to `canonical_for(category)`
   when a canonical set exists; either reject the write or auto-repair the
   outcome set to the canonical membership. This catches both LLM drift and
   stale pre-catalog packs at the source.
2. **On-demand evaluator.** A check that, given a stored session, flags whether
   its `character_set` matches the now-canonical set, to drive a backfill/repair
   of historical `session_history` rows (the "pre-catalog stored pack" failure
   mode noted above).

The open design question is **reject vs. auto-repair** (and whether to backfill
historical rows or only gate new writes); that needs an owner call before build.

---

## DELIVERED — canonical growth machinery (`feat/canonical-growth`)

The "Next steps" above are now built on the `feat/canonical-growth` branch
(stacked on this PR). Owner decision recorded: **reject-to-quarantine, NO
auto-repair**; gate **new writes** only (no historical backfill). See
`specifications/audit/CANONICAL-CORRECTNESS-EVALUATOR.md` for the full design.

1. **On-demand correctness evaluator** —
   `backend/scripts/eval_canonical_correctness.py` (READ-ONLY). Canonical topics:
   exact (single) / palette-consistent (blended) set comparison, no LLM. Non-
   canonical topics: LLM judge via the existing `llm_service` (cheap model,
   `--max-spend` USD cap, fail-safe). Scans `session_history` (`--since/--limit`)
   or an explicit `--input` list.
2. **Persist-time canonical gate** —
   `backend/app/services/precompute/canonical_gate.py`, wired into
   `builder.run_build` (mismatch → job REJECTED with `canonical_mismatch` + diff,
   artefact NOT persisted) and `evaluator.assert_canonical` (hard blocking
   reason). Blend-aware: a DISC blend passes, a wrong-named set fails.
3. **Drift fix**: `_merge_config` now inherits the code-catalog `outcome_mode`
   when App-Config omits it, so a silently-dropped `blended` marker can't make
   the gate reject a legitimate DISC/Big-Five blend.

## Canonical growth queue / backlog (living)

The **expansion pipeline** (`backend/scripts/canonical_growth_queue.py`,
READ-ONLY) surfaces the most-frequent non-canonical `session_history.category`
values (grouped by normalized key) so the owner can curate + add them. Run it to
refresh this backlog:

```bash
python -m scripts.canonical_growth_queue --since 90d --top 50 --markdown
```

### Curation backlog (seed)

Live prod rows could not be enumerated from the repo (no DB dump checked in), so
this seed combines (a) the audit's recommended high-value adds and (b) the
App-Config-only drift-risk sets that should be promoted into the reviewed CODE
catalog. The growth-queue tool will append the actual most-popular non-canonical
topics once run against the live DB.

| Topic | Bounded? | Status | Recommended action |
|---|---|---|---|
| Greek gods (Twelve Olympians) | 12 | not canonical | **ADD to code catalog** (membership in this PR's audit) |
| Generations (Lost…Gen Alpha) | 6–8 | not canonical | **ADD to code catalog** (document the Gen-Alpha boundary source) |
| Seven Deadly Sins | 7 | App-Config only | **promote to code catalog** (drift-proof) |
| Chakras (Seven) | 7 | App-Config only | **promote to code catalog** |
| Wu Xing | 5 | App-Config only | promote to code catalog |
| Ayurvedic Doshas | 3 | App-Config only | promote to code catalog |
| Classical Elements (Greek, 4/5) | 4–5 | App-Config only | promote to code catalog (disambiguate 4 vs 5/Aether) |
| Seven Heavenly Virtues | 7 | App-Config only | promote to code catalog |
| Platonic Solids | 5 | App-Config only | promote to code catalog |
| Marvel / Star Wars alignment | — | not canonical | **skip** — open character/faction list, not a bounded taxonomy; leave to the media-character path |

> Note: "Seven Deadly Sins" and "Chakras" currently DO resolve (via App-Config),
> so the growth-queue tool correctly excludes them as already-canonical; they are
> listed here only as **drift-proofing** adds for the CODE catalog. The genuinely
> non-canonical, high-value gaps are **Greek gods** and **Generations**.
