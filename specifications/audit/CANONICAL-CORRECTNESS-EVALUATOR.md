# Canonical Correctness — On-Demand Evaluator + Persist-Time Gate

Branch: `feat/canonical-growth` (stacks on `feat/canonical-correctness`, PR #43).

This documents the canonical **growth machinery** built on top of the canonical
seed: an on-demand correctness evaluator, a nightly persist-time gate, and a
growth backlog. Owner's invariant: *"our canonical set only improves the more we
add to it."*

---

## 1. On-demand correctness evaluator (READ-ONLY)

`backend/scripts/eval_canonical_correctness.py` — run manually, as needed
(despite cost), to audit whether the character/outcome set chosen for a topic is
correct. It **never** mutates quizzes.

### What it checks

- **Canonical topics** (`canonical_for(topic)` non-empty): a pure set comparison
  — **no LLM, no cost**.
  - `outcome_mode='single'` (MBTI, Hogwarts, Enneagram, …): the set must EXACTLY
    equal the canonical set (order-independent, case/accent-folded). Reports the
    `missing` / `extra` diff on a mismatch.
  - `outcome_mode='blended'` (DISC, Big Five / OCEAN): PALETTE-consistent — every
    outcome must be a canonical dimension, but a *blend* (not exactly-one, not
    all-of-N) PASSES. A wrong-named (off-palette) entry FAILS, reporting the
    `off_palette` diff.
- **Non-canonical topics**: an **LLM judge** via the app's existing
  `llm_service` (a cheap, already-configured model — default
  `gemini/gemini-flash-latest`; no new provider/key/dependency) scores 1-10
  whether the set is correct + appropriate for the topic, with a one-line reason.
  - **Cost cap**: `--max-spend <USD>` bounds total judge spend with a fail-safe
    ledger (mirrors the precompute cost-guard). Once the next projected call
    would exceed the cap, the remaining non-canonical topics are reported as
    `skipped-budget` rather than judged.
  - **Fail-safe**: an LLM outage yields `judge-unavailable` (never a silent
    pass) and is **not** charged.

### Inputs

- `--since 30d --limit 200`: scan STORED quizzes from `session_history`
  (`category` + `character_set` JSONB), most-recent first. DSN from
  `DATABASE_URL` (or `settings.DATABASE_URL`). READ-ONLY SELECT only.
- `--input pairs.json`: an explicit offline list of
  `[{"topic": ..., "character_set": [...]}, ...]` (no DB needed).

### Output

A counts summary + per-quiz table (`--json` for machine-readable). Verdict
buckets: `canonical-correct` / `canonical-mismatch` / `non-canonical-good` /
`non-canonical-flagged` / `skipped-budget` / `judge-unavailable`. Exit code is
**1** when any `canonical-mismatch` is found (CI / nightly friendly), else 0.

### Exact commands

```bash
# Canonical checks only on the last 30 days (no LLM, no cost):
python -m scripts.eval_canonical_correctness --since 30d --limit 200 --no-judge

# Scan + LLM-judge non-canonical topics, cheap model, hard $2.00 cap:
python -m scripts.eval_canonical_correctness \
    --since 14d --limit 500 \
    --judge-model gemini/gemini-flash-latest --max-spend 2.00

# Offline, from an explicit list, machine-readable:
python -m scripts.eval_canonical_correctness --input pairs.json --json
```

Run from `backend/` with the app env set, e.g.
`APP_ENVIRONMENT=local LOG_TO_FILE=false python -m scripts.eval_canonical_correctness ...`.

---

## 2. Nightly persist-time canonical gate (reject-to-quarantine)

Shared, pure, blend-aware comparison core:
`backend/app/services/precompute/canonical_gate.py`.

- **Builder** (`builder.run_build`, BEFORE `persist_fn`): for a topic in the
  reviewed canonical catalog, the artefact's outcome set MUST match canonical —
  EXACT for `single`, PALETTE-consistent (blend-tolerant) for `blended`. On a
  mismatch the job is routed to quarantine (transitioned to `REJECTED` with
  `error_text="canonical_mismatch: <diff>"`) and the artefact is **NOT**
  persisted. Reject-to-quarantine only — **NO auto-repair**. Non-canonical
  topics are a no-op (the LLM judge owns those, not this gate).
- **Evaluator** (`evaluator.assert_canonical`): the same assertion is surfaced as
  a hard `canonical_mismatch` **blocking reason**, so a high judge score can
  never promote a canonically-wrong set (mirrors `assert_tier3_sources`).

### What the gate rejects — worked example

Topic `"Hogwarts Houses"` (`outcome_mode='single'`). Artefact outcome set
`[Gryffindor, Slytherin]` →
**rejected**: `canonical_mismatch: missing=['Hufflepuff', 'Ravenclaw']; extra=[]`.
The full correct set `[Gryffindor, Slytherin, Ravenclaw, Hufflepuff]` passes.

Topic `"DISC"` (`outcome_mode='blended'`). A blend `[Dominance, Influence]`
**passes** (blends are allowed for the upcoming blended-DISC feature). A
wrong-named set `[Director, Inspirer]` →
**rejected**: `canonical_mismatch: off_palette=['Director', 'Inspirer']; palette=[...]`.

### Blend-awareness drift fix

App-Config YAML defines some marquee sets (e.g. Big Five) WITHOUT the
`outcome_mode` marker; the previous `sets` overlay fully replaced the code entry
and silently dropped `blended`, which would have made the gate wrongly *reject* a
legitimate Big-Five/DISC blend. `canonical_sets._merge_config` now inherits
`outcome_mode` from the code catalog when the App-Config overlay omits it (the
code catalog is the floor for the marker, just as it already is for aliases).

---

## 3. Expansion pipeline — canonical growth queue

`backend/scripts/canonical_growth_queue.py` (READ-ONLY) surfaces the most
frequent `session_history.category` values that have **no** `canonical_for`
match, grouped by the canonical noise-normalized key (so "What are the greek
gods", "greek gods quiz", "Greek gods" fold into one bucket). This is the
curation on-ramp: the owner reviews the queue and adds high-value bounded
taxonomies to the reviewed code catalog.

```bash
python -m scripts.canonical_growth_queue --since 90d --top 30
python -m scripts.canonical_growth_queue --since 90d --top 50 --markdown   # for the backlog doc
```

The living backlog is `specifications/audit/CANONICAL-COVERAGE-2026-06-30.md`
(see its "Canonical growth queue / backlog" section).
