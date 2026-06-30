# Image-quality eval: actual-image vision-judge harness

`backend/scripts/eval_image_quality.py` scores the **actual rendered pixels** of
our generated images using a **vision-capable LLM**, and gates a run on a
pass-rate so it is CI/owner-usable.

## Why it exists (the gap it closes)

The owner asked for image quality in our evaluation set. The existing image judge
in `backend/scripts/generate_images_for_packs.py` scores the **prompt + character
concept**, NOT the pixels — its own docstring says it does "not [look] at the
image URL directly, since LLMs cannot browse external image URLs." So a deformed
face, blank/placeholder tile, garbled text, or off-topic render can pass the
concept judge and ship. This harness fetches each image and sends the real bytes
to a multimodal model that judges the rendered result.

### How it complements the other evals
| Tool | What it scores | When |
|------|----------------|------|
| `generate_images_for_packs.py` | prompt / character concept | at GENERATION time |
| `evals/` (`quizzical_evals`) | TEXT artifacts (synopsis, characters, questions, profile) via LLM-as-judge | per agent-function sweep |
| **`eval_image_quality.py`** (new) | **rendered image PIXELS** (fidelity/relevance/style/blockers) | after generation/backfill, or periodically over MediaAsset rows |

## Scoring rubric (per image)
- `fidelity` 1-10 — clean, well-rendered (not deformed / blurry / artifacted)
- `relevance` 1-10 — matches the subject & topic
- `style_ok` bool — on-brand, single coherent illustrated portrait
- `blocking_reasons` — any of: `deformed_face`, `off_topic`, `placeholder_or_blank`,
  `text_garbage`, `ip_violation`

**Pass** = `fidelity >= 7` AND `relevance >= 7` AND `style_ok` AND no blocking reasons.

Verdicts: `pass` | `fail` | `unavailable` (dead/expired/missing image — never a
silent pass) | `skipped-budget` (`--max-spend` reached) | `error` (judge call failed).

## Which model handles the image + how
A **direct LiteLLM `acompletion`** (chat-completions) multimodal call — the
vision path both **gpt-4o** (default) and **gemini/gemini-flash-latest** accept.
We do NOT route through `llm_service` (that wrapper targets the Responses API for
structured TEXT). The fetched image bytes are sent as an `image_url` content part
holding a base64 `data:` URL, so the model sees the real pixels. The judge returns
strict JSON, parsed tolerantly (garbage -> failing score, never a silent pass).

## Exact commands
```bash
# from backend/

# (a) hand-built pair list, gpt-4o, $2 cap, JSON report:
OPENAI_API_KEY=sk-... python -m scripts.eval_image_quality \
    --input pairs.json --judge-model gpt-4o --max-spend 2.00 --json report.json

# (b) recent DB images (READ-ONLY), Gemini judge, CI gate at 0.85:
EVAL_DB_URL=postgresql://... GEMINI_API_KEY=... \
python -m scripts.eval_image_quality \
    --media-from-db --since 2026-06-01 --limit 100 \
    --judge-model gemini/gemini-flash-latest --min-pass-rate 0.85

# (c) local folder + sidecar subjects.json, opt in to persist scores:
OPENAI_API_KEY=sk-... python -m scripts.eval_image_quality \
    --dir ./out_images --write-scores --max-spend 1.00
```

`pairs.json` shape (list, or `{"items": [...]}`):
```json
[{"image_url": "https://...", "subject": "Gandalf", "topic": "LOTR",
  "expected_description": "old wizard, grey robe, staff"}]
```
`--dir` sidecar `subjects.json` shape:
```json
{"hero.png": {"subject": "Hero", "topic": "Demo", "expected_description": "..."}}
```

## Cost safety + read-only
- `--max-spend` USD ledger (reuses `scripts._precompute_spend.SpendLedger`).
  Before each judge call the projected cost is checked; once the cap would be
  exceeded the run STOPS and every remaining image is `skipped-budget` (never
  silently passed). Each vision call is charged as 5 text-judge units (~$0.01) so
  the cap reflects true vision spend.
- **READ-ONLY by default.** Never writes images or DB rows. `--write-scores` is an
  explicit opt-in that updates `MediaAsset.evaluator_score` (1-10, from `fidelity`)
  for DB-sourced rows only.

## Exit code (CI gate)
`0` when pass-rate (over judged images: pass/fail/unavailable/error) `>= --min-pass-rate`
(default `0.85`); `1` otherwise. `skipped-budget` images are excluded from the
denominator (never evaluated).

## Keys / access needed to run on REAL images
- A **vision-capable LLM key**: `OPENAI_API_KEY` (gpt-4o) OR `GEMINI_API_KEY`
  (gemini/gemini-flash-latest). LiteLLM reads these from the environment.
- **Access to real generated images** — which only exist if the prod **FAL key**
  (`FAL_KEY` / `settings.images`) has been used to generate them. This script
  needs NO FAL key itself (it only READS images).
- For `--media-from-db`: additionally `EVAL_DB_URL` (or `PROD_DB_URL`) pointing at
  a DB that has MediaAsset rows. The query is a read-only `SELECT` joining
  `characters.image_asset_id -> media_assets.id` for the depicted subject.

## Tests
`backend/tests/unit/scripts/test_eval_image_quality.py` — a FAKE vision client +
fake httpx client (no network/LLM). Covers verdict logic, the `--max-spend`
fail-safe + `skipped-budget` remainder, dead-URL -> `unavailable`, the pass-rate
gate + exit code, the read-only default, and the tolerant JSON parser.
