"""Same-universe Q&A image RELEVANCE-GATE evaluator (the make-or-break metric).

Runs the PRODUCTION ``RelevanceGate`` (``app.services.icons.relevance_gate``)
with the REAL 384-dim ``BAAI/bge-small-en-v1.5`` embedder over a diverse,
hand-labeled Q&A sample (``qa_relevance_labeled.json``) and reports honest
routing quality:

  * precision@gate  — of strings we ROUTE to FAL, how many were truly concrete
                      (1 - false-positive rate among predicted-positive). This is
                      the budget-protection number: FPs = wasted FAL spend on
                      abstract strings.
  * recall          — of truly-concrete strings, how many we routed (coverage of
                      the images worth making).
  * fp_rate         — abstract strings wrongly routed to FAL / all abstract.
  * coverage        — fraction of ALL strings routed to FAL.
  * accuracy        — overall label agreement.

It SWEEPS (margin, concrete_floor) to surface the operating point that maximises
precision (protect budget) while keeping recall reasonable, then re-runs the
default config and writes:

  * ``qa_relevance_eval.json`` — metrics at the configured operating point +
    the full sweep + every misclassification (for honest error inspection).

Reproduce (backend venv, model downloads once):
    cd backend && APP_ENVIRONMENT=local LOG_TO_FILE=false \
      .venv312/Scripts/python.exe ../specifications/prototype/qa_relevance_eval.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_BACKEND = _REPO / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

OUT_DIR = Path(__file__).resolve().parent
LABELED = OUT_DIR / "qa_relevance_labeled.json"

# BGE asymmetric retrieval prefix — the SAME one the binder/gate use in prod.
QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


def _metrics(rows: list[dict]) -> dict:
    """rows: [{should_generate, generate}]. Compute confusion + headline rates."""
    tp = fp = tn = fn = 0
    for r in rows:
        truth = bool(r["should_generate"])
        pred = bool(r["generate"])
        if truth and pred:
            tp += 1
        elif not truth and pred:
            fp += 1
        elif not truth and not pred:
            tn += 1
        else:
            fn += 1
    n = tp + fp + tn + fn
    pos_pred = tp + fp
    pos_true = tp + fn
    neg_true = tn + fp
    return {
        "n": n,
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "precision": round(tp / pos_pred, 4) if pos_pred else None,
        "recall": round(tp / pos_true, 4) if pos_true else None,
        "fp_rate": round(fp / neg_true, 4) if neg_true else None,
        "coverage": round(pos_pred / n, 4) if n else 0.0,
        "accuracy": round((tp + tn) / n, 4) if n else 0.0,
    }


async def _score_all(items: list[dict]) -> list[dict]:
    """Embed once per string, then we can re-threshold cheaply for the sweep."""
    from app.services.icons.embedder import raw_embed
    from app.services.icons.relevance_gate import (
        _ANCHORS,
        _MIN_CHARS,
        _looks_template,
        _max_sim,
    )
    from app.services.precompute.lookup import _default_cosine

    concrete, abstract = await _ANCHORS.get(raw_embed, QUERY_PREFIX)
    scored: list[dict] = []
    for it in items:
        text = it["text"]
        # Reproduce the gate's cheap pre-filters so the sweep matches prod
        # (template check first, then the min-length floor).
        if not text.strip() or _looks_template(text) or len(text.strip()) < _MIN_CHARS:
            scored.append({**it, "concrete_sim": 0.0, "abstract_sim": 0.0,
                           "pre_skipped": True})
            continue
        q = await raw_embed(QUERY_PREFIX + text)
        c = _max_sim(list(q), concrete, _default_cosine)
        a = _max_sim(list(q), abstract, _default_cosine)
        scored.append({**it, "concrete_sim": round(c, 4),
                       "abstract_sim": round(a, 4), "pre_skipped": False})
    return scored


def _apply(scored: list[dict], *, margin: float, floor: float) -> list[dict]:
    out = []
    for s in scored:
        if s.get("pre_skipped"):
            gen = False
        else:
            c, a = s["concrete_sim"], s["abstract_sim"]
            gen = (c >= floor) and ((c - a) >= margin)
        out.append({**s, "generate": gen})
    return out


def _sweep(scored: list[dict]) -> list[dict]:
    grid = []
    for margin in (0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10):
        for floor in (0.20, 0.22, 0.25, 0.28, 0.30, 0.33):
            applied = _apply(scored, margin=margin, floor=floor)
            m = _metrics(applied)
            grid.append({"margin": margin, "concrete_floor": floor, **m})
    return grid


def _best(grid: list[dict]) -> dict:
    """Pick the operating point: maximise precision, then recall, then coverage,
    requiring precision==1.0 if achievable (zero wasted FAL spend) with recall>=0.7."""
    perfect = [g for g in grid
               if g["precision"] == 1.0 and (g["recall"] or 0) >= 0.7]
    pool = perfect or grid
    return max(
        pool,
        key=lambda g: (
            g["precision"] or 0,
            g["recall"] or 0,
            -g["margin"],   # prefer the laxer margin among ties (more headroom)
        ),
    )


async def main() -> None:
    doc = json.loads(LABELED.read_text(encoding="utf-8"))
    items = doc["items"]
    n_pos = sum(1 for i in items if i["should_generate"])
    print(f"labeled sample: {len(items)} strings "
          f"({n_pos} concrete / {len(items) - n_pos} abstract)")

    scored = await _score_all(items)
    grid = _sweep(scored)
    best = _best(grid)

    # Production default operating point (matches RelevanceGateConfig defaults).
    DEFAULT_MARGIN, DEFAULT_FLOOR = 0.04, 0.20
    default_applied = _apply(scored, margin=DEFAULT_MARGIN, floor=DEFAULT_FLOOR)
    default_metrics = _metrics(default_applied)

    misclassified = [
        {"topic": r["topic"], "kind": r["kind"], "text": r["text"],
         "should_generate": r["should_generate"], "generate": r["generate"],
         "concrete_sim": r["concrete_sim"], "abstract_sim": r["abstract_sim"],
         "margin": round(r["concrete_sim"] - r["abstract_sim"], 4)}
        for r in default_applied
        if bool(r["should_generate"]) != bool(r["generate"])
    ]

    out = {
        "model": "BAAI/bge-small-en-v1.5",
        "query_prefix": QUERY_PREFIX,
        "n_labeled": len(items),
        "n_concrete": n_pos,
        "n_abstract": len(items) - n_pos,
        "default_operating_point": {
            "margin": DEFAULT_MARGIN, "concrete_floor": DEFAULT_FLOOR,
            "metrics": default_metrics,
        },
        "best_operating_point": best,
        "sweep": grid,
        "misclassified_at_default": misclassified,
    }
    (OUT_DIR / "qa_relevance_eval.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print("\n=== DEFAULT (margin=%.2f, floor=%.2f) ===" % (DEFAULT_MARGIN, DEFAULT_FLOOR))
    print(json.dumps(default_metrics, indent=2))
    print("\n=== BEST (by precision, then recall) ===")
    print(json.dumps(best, indent=2))
    print(f"\nmisclassified at default: {len(misclassified)}")
    for m in misclassified:
        tag = "FP (wasted $)" if m["generate"] else "FN (missed img)"
        print(f"  [{tag}] {m['text']!r}  c={m['concrete_sim']} a={m['abstract_sim']}")


if __name__ == "__main__":
    asyncio.run(main())
