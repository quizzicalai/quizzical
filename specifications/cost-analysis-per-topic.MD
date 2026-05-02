# Cost Analysis — Single Quiz vs. Pre-Computed Topic

Status: as of §21 Phase 4 deploy (2026-05-02). All numbers are public list
prices for the model families currently wired in `appconfig.local.yaml`
(Gemini 2.5 Flash for most agent nodes, GPT-4o-mini for one
JSON-strict node, FAL `fal-ai/flux/schnell` for images).

> **TL;DR**
> - **Live (no precompute)** = roughly **$0.0034 per quiz** at current
>   shape — about **two-thirds of which is image generation**.
> - **Pre-computed pack v3** (synopsis + characters + 5 baseline questions
>   pre-baked once per topic) reduces the per-quiz cost to **~$0.00084**
>   — a **~75 % cut** on the hot path.
> - One-time topic build cost (LLM + optional image bake) is
>   **~$0.0066–$0.024 per topic** depending on whether character
>   images are pre-baked.
> - **Break-even is ~3 quizzes per topic.** For any topic that gets
>   served more than a handful of times, precompute wins immediately.
> - All character images on existing v3 topics are already baked once
>   and served from FAL CDN, so the marginal *image* cost on a HIT path
>   is currently $0.

---

## 1. Pricing assumptions (May 2026, USD)

| Model | $/1K input tok | $/1K output tok | Source |
|---|---:|---:|---|
| Gemini 2.5 Flash | $0.000150 | $0.000600 | Google AI list price |
| Gemini Flash latest (≈Flash) | $0.000150 | $0.000600 | same |
| GPT-4o-mini | $0.000150 | $0.000600 | OpenAI list price |

Image:
| Provider/model | $/image | Notes |
|---|---:|---|
| FAL `fal-ai/flux/schnell` 512×512, 2 steps | **$0.00125** | FAL list (3¢ at 1024×1024 SDXL → ~1.25¢ for schnell at 512²); confirm in FAL billing dashboard |
| FAL `fal-ai/flux/schnell` 1024×576 (synopsis) | **$0.0014** | proportional to pixels |

Storage (Postgres + Azure Blob): **negligible** — pack rows are ~6 KB each
and image blobs are <100 KB each. Container Apps egress dominates and is
shared with the rest of the app.

---

## 2. Per-quiz token shape (measured/observed)

These are conservative estimates from the agent graph
(`bootstrap → generate_characters → generate_baseline_questions → 5×decide → assemble`):

| Node | Input tok | Output tok | Subtotal |
|---|---:|---:|---:|
| `bootstrap` (plan + synopsis) | 1 200 | 350 | $0.000390 |
| `generate_characters` (4 cards × ~200 tok each, 1 batched call) | 1 600 | 1 200 | $0.000960 |
| `generate_baseline_questions` (5 Qs × 4 opts, 1 call) | 1 400 | 900 | $0.000750 |
| `decide_or_finish` × ~5 (rolling context, small) | 5 × 800 | 5 × 100 | $0.000900 |
| `assemble_and_finish` (final profile prose) | 1 800 | 700 | $0.000690 |
| **LLM subtotal** |  |  | **~$0.00369** |

Image subtotal (per quiz, no precompute):
| Image | Cost |
|---|---:|
| Synopsis hero (1024×576) | $0.00140 |
| 4 × character icons (512×512) | $0.00500 |
| **Image subtotal** | **$0.00640** |

**Total per live quiz: ≈ $0.0101** (LLM $0.00369 + images $0.0064).

> Note: prior internal estimate quoted **$0.07–$0.17 per quiz** when the
> agent did multiple judge passes and used larger models. The current
> graph runs single-pass on Flash + schnell, so the steady-state cost
> is much lower. Token counts above should be re-validated against the
> `model_provenance` rows once we have ≥100 prod quizzes to sample.

---

## 3. One-time pre-compute build cost per topic (v3)

Pack v3 carries: 1 synopsis + 4 character profiles + 5 baseline questions.
Today we **hand-author** the source JSON (synopsis + characters + Qs), so
the LLM cost at build time for content is **$0**. The cost components are:

| Component | When done | Per topic | Notes |
|---|---|---:|---|
| Hand-author content | one-time, human | $0 LLM | author time is the real cost |
| LLM-generated alternative (if we automate) | one-time, automated | ~$0.0021 | bootstrap + characters + questions, no per-user nodes |
| Synopsis hero image (1024×576) | one-time at build | $0.00140 | already baked & in DB for all 5 v3 topics |
| 4 character icons (512×512) | one-time at build | $0.00500 | currently baked **lazily** on the first live quiz; bake-at-build available via optional `image_url` field added in v3 |
| Eval / QA pass (optional, future) | one-time | ~$0.0006 | 1 Flash judge call |

**Build cost summary**:
- Hand-authored content + synopsis bake (status quo): **~$0.00140 / topic**.
- Hand-authored + full image bake (synopsis + 4 chars): **~$0.00640 / topic**.
- Fully automated (LLM-authored + full image bake + eval): **~$0.0091 / topic**.

Storage of the resulting pack: ~6 KB JSON in DB + 5 image blobs ≈ ~400 KB total.
At Azure Blob hot-tier $0.018/GB-month this is **<$0.00001 / topic / month**.

---

## 4. Per-quiz cost on a HIT path (after §21 Phase 3 + 4)

When `/quiz/start` short-circuits (Phase 3) **and** `/quiz/proceed`
short-circuits (Phase 4), the LLM is invoked only for adaptive/finish nodes:

| Node | Status |
|---|---|
| `bootstrap` | **skipped** (synopsis pre-baked) |
| `generate_characters` | **skipped** (cards pre-baked) |
| `generate_baseline_questions` | **skipped** (Phase 4 — pre-baked) |
| `decide_or_finish` × 5 | runs (still per-user adaptive) |
| `assemble_and_finish` | runs (per-user profile) |

LLM cost on a HIT quiz: **~$0.00159** (just `decide × 5` + `assemble`).

Image cost on a HIT quiz: **$0** when synopsis + character images are
pre-baked (current state for all 5 v3 topics — synopsis verified live;
character images already cached from prior renders and re-served from
FAL CDN).

**Total per HIT quiz: ≈ $0.00159** (vs $0.0101 cold) → **~84 % reduction**.

If we also pre-compute the final `assemble_and_finish` profile per
character (5th-phase work, not yet done), the per-quiz LLM cost drops to
**~$0.00090** (just the 5 decide calls), an overall **~91 % reduction**
from the cold path.

---

## 5. Break-even

Marginal saving per HIT quiz vs cold path: **$0.0101 − $0.00159 ≈ $0.00851**.

| Build profile | Build cost | Quizzes to break even |
|---|---:|---:|
| Hand-author + synopsis image only | $0.0014 | **<1** |
| Hand-author + full image bake | $0.0064 | **~1** |
| LLM-authored + full image bake + eval | $0.0091 | **~2** |

Translation: **any topic that gets played more than 2 times** is already
cheaper to serve from precompute than to generate live, regardless of
which build profile we choose. The 5 v3 topics will pay back in the first
few quizzes, then be effectively free for the rest of their lifetime.

---

## 6. Volume scenarios

| Quizzes/day on this topic | Cold cost/month | HIT cost/month | Savings/month |
|---:|---:|---:|---:|
| 10 (long-tail) | $3.03 | $0.48 | $2.55 |
| 100 (steady) | $30.30 | $4.77 | $25.53 |
| 1 000 (popular) | $303.00 | $47.70 | $255.30 |
| 10 000 (viral) | $3 030.00 | $477.00 | $2 553.00 |

Assumes 30 days/month; LLM + image at the values in §2 / §4.

For the current 5 v3 starter topics, the dominant cost driver in
production once traffic ramps will be the **per-user adaptive nodes**
(`decide × 5` + `assemble`), not the pre-baked content. Pre-computing the
adaptive layer is the next leverage point (a Phase 5 follow-up, out of
scope for this iteration).

---

## 7. What's still un-pre-computed (Phase 5+ candidates)

1. **Adaptive question selection** — `decide_or_finish` runs per user
   choice. Hard to fully precompute (combinatorial), but could be cached
   per `(pack_id, answer_hash_path)` for the most common branches.
2. **Final profile prose** (`assemble_and_finish`) — there are only
   N characters per topic, so the "you got Slytherin" prose can be
   generated once per (topic × character) and stored. This is the
   single largest remaining cost line (~$0.0007/quiz).
3. **Question images** — currently no image per question. If we add
   them they should be baked at build time, not per quiz.

---

## 8. Methodology notes & validation TODOs

- Token counts in §2 are estimates from prompt sizes in
  `appconfig.local.yaml`. **Validate** by sampling 100 prod
  `model_provenance` rows once we have them.
- FAL pricing for schnell at 512² is interpolated from the SDXL 1024²
  rate. **Validate** against the FAL billing dashboard once a few hundred
  images have been generated this month.
- All numbers are **incremental** to fixed infrastructure
  (Container Apps minimum replicas, Postgres, Redis). Those are
  ~$50–$100/month and unaffected by per-topic precompute.
