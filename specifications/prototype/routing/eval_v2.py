"""Upgraded routing eval (round 2).

What's new vs router.py::evaluate (and why):
  * DUAL-PERSPECTIVE scoring — reports precision/coverage/FP under BOTH the strict
    and the lenient annotator, bracketing the true number instead of trusting one
    person. (Fixes the skeptic's "single-annotator artifact" critique.)
  * INTER-ANNOTATOR AGREEMENT — Cohen's kappa between the two label perspectives on
    the per-item {show-correct / show-wrong / no-show} decision at the operating tau.
  * STRATIFIED SLICES — precision/coverage/FP broken out by kind (question vs answer),
    abstractness (concrete/abstract/trap), and len_bucket, so we see WHERE it fails.
  * MARGIN GATE (router improvement) — optional second gate: only show an icon if
    top_sim >= tau AND (top_sim - second_sim) >= delta. The margin kills "everything
    is weakly similar" false positives (homonym traps, abstract phrases) that a flat
    tau lets through. Sweeps (tau, delta) jointly.
  * Works off qa_labeled_master.json (354 items) by default.

Usage:
  python routing/eval_v2.py --backend local --captions rich
  python routing/eval_v2.py --backend local --captions rich --margin   # enable margin gate sweep
  python routing/eval_v2.py --backend local --captions name             # caption ablation
  python routing/eval_v2.py --backend openai --captions rich
Outputs JSON to data/eval2_<backend>_<captions>[_margin].json and prints a summary.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

import sys
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from router import make_embedder, load_catalog, caption_for  # noqa: E402

DATA = HERE.parent / "data"


def load_master() -> list[dict]:
    return json.loads((DATA / "qa_labeled_master.json").read_text(encoding="utf-8"))["items"]


def route_top2(query_vecs: np.ndarray, cap_vecs: np.ndarray):
    """Return (best_idx, best_sim, second_sim) per query."""
    sims = query_vecs @ cap_vecs.T  # [Q, C], cosine (L2-normalised inputs)
    order = np.argsort(-sims, axis=1)
    best_idx = order[:, 0]
    rng = np.arange(sims.shape[0])
    best_sim = sims[rng, best_idx]
    second_sim = sims[rng, order[:, 1]]
    return best_idx, best_sim, second_sim


def classify(item_expected, show, top_concept):
    """Return one of: 'tp' (shown & correct), 'fp' (shown & wrong / shown on no-icon),
    'miss' (eligible but not shown), 'tn' (no-icon and not shown)."""
    eligible = bool(item_expected)
    if show:
        if eligible and top_concept in item_expected:
            return "tp"
        return "fp"
    else:
        return "miss" if eligible else "tn"


def metrics_for(items, best_idx, best_sim, second_sim, concepts, tau, delta, perspective):
    key = f"expected_{perspective}"
    tp = fp = miss = tn = 0
    decisions = []  # per-item decision code for kappa
    for i, it in enumerate(items):
        show = best_sim[i] >= tau and (best_sim[i] - second_sim[i]) >= delta
        cls = classify(it[key], show, concepts[best_idx[i]])
        decisions.append(cls)
        if cls == "tp":
            tp += 1
        elif cls == "fp":
            fp += 1
        elif cls == "miss":
            miss += 1
        else:
            tn += 1
    shown = tp + fp
    n_eligible = sum(1 for it in items if it[key])
    precision = tp / shown if shown else 0.0
    fp_rate = fp / len(items)
    coverage = tp / n_eligible if n_eligible else 0.0
    no_icon_rate = (miss + tn) / len(items)
    return {
        "precision_at_1": round(precision, 4),
        "fp_rate": round(fp_rate, 4),
        "coverage": round(coverage, 4),
        "no_icon_rate": round(no_icon_rate, 4),
        "tp": tp, "fp": fp, "miss": miss, "tn": tn, "shown": shown,
    }, decisions


def cohens_kappa(a: list[str], b: list[str]) -> float:
    """Kappa over the per-item decision label (tp/fp/miss/tn collapsed to the
    annotator-visible outcome: show-correct / show-wrong / no-show)."""
    def collapse(x):
        if x == "tp":
            return "show_correct"
        if x == "fp":
            return "show_wrong"
        return "no_show"  # miss or tn
    A = [collapse(x) for x in a]
    B = [collapse(x) for x in b]
    cats = sorted(set(A) | set(B))
    n = len(A)
    po = sum(1 for x, y in zip(A, B) if x == y) / n
    pe = sum((A.count(c) / n) * (B.count(c) / n) for c in cats)
    return (po - pe) / (1 - pe) if (1 - pe) else 1.0


def pick_op(sweep, perspective):
    """Max coverage s.t. fp<=0.05 and precision>=0.80 on the chosen perspective."""
    feas = [s for s in sweep
            if s[perspective]["fp_rate"] <= 0.05 and s[perspective]["precision_at_1"] >= 0.80]
    if feas:
        return max(feas, key=lambda s: s[perspective]["coverage"])
    fp_ok = [s for s in sweep if s[perspective]["fp_rate"] <= 0.05]
    return max(fp_ok, key=lambda s: s[perspective]["coverage"]) if fp_ok else None


def slice_metrics(items, best_idx, best_sim, second_sim, concepts, tau, delta, perspective, axis):
    out = {}
    vals = sorted(set(it.get(axis, "?") for it in items))
    for v in vals:
        sub = [it for it in items if it.get(axis, "?") == v]
        idx = [i for i, it in enumerate(items) if it.get(axis, "?") == v]
        m, _ = metrics_for(sub, best_idx[idx], best_sim[idx], second_sim[idx],
                           concepts, tau, delta, perspective)
        out[v] = {k: m[k] for k in ("precision_at_1", "fp_rate", "coverage", "shown", "tp", "fp")}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="local", choices=["local", "openai"])
    ap.add_argument("--captions", default="rich", choices=["rich", "name"])
    ap.add_argument("--margin", action="store_true", help="enable margin-gate (tau,delta) sweep")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    emb = make_embedder(args.backend)
    catalog = load_catalog()
    items = load_master()
    concepts = [ic["concept"] for ic in catalog]
    captions = [caption_for(ic, args.captions) for ic in catalog]

    cap_vecs = emb.embed(captions)
    q_vecs = emb.embed([it["text"] for it in items])
    best_idx, best_sim, second_sim = route_top2(q_vecs, cap_vecs)

    taus = [round(x, 2) for x in np.arange(0.40, 0.86, 0.02)]
    deltas = [0.0, 0.02, 0.04, 0.06, 0.08] if args.margin else [0.0]

    sweep = []
    for tau in taus:
        for delta in deltas:
            ms, dec_s = metrics_for(items, best_idx, best_sim, second_sim, concepts, tau, delta, "strict")
            ml, dec_l = metrics_for(items, best_idx, best_sim, second_sim, concepts, tau, delta, "lenient")
            sweep.append({"tau": tau, "delta": delta, "strict": ms, "lenient": ml,
                          "kappa": round(cohens_kappa(dec_s, dec_l), 4)})

    op_strict = pick_op(sweep, "strict")
    op_lenient = pick_op(sweep, "lenient")

    # stratified slices at the strict operating point
    slices = {}
    if op_strict:
        t, d = op_strict["tau"], op_strict["delta"]
        for axis in ("kind", "abstractness", "len_bucket"):
            slices[axis] = {
                "strict": slice_metrics(items, best_idx, best_sim, second_sim, concepts, t, d, "strict", axis),
                "lenient": slice_metrics(items, best_idx, best_sim, second_sim, concepts, t, d, "lenient", axis),
            }

    res = {
        "backend": emb.name,
        "caption_mode": args.captions,
        "margin_gate": args.margin,
        "n_items": len(items),
        "n_eligible_strict": sum(1 for it in items if it["expected_strict"]),
        "n_eligible_lenient": sum(1 for it in items if it["expected_lenient"]),
        "n_icons": len(catalog),
        "operating_point_strict": op_strict,
        "operating_point_lenient": op_lenient,
        "slices_at_strict_op": slices,
        "sweep": sweep,
    }

    out = args.out or str(DATA / f"eval2_{args.backend}_{args.captions}{'_margin' if args.margin else ''}.json")
    Path(out).write_text(json.dumps(res, indent=2), encoding="utf-8")

    # human summary
    print(f"backend={res['backend']} captions={res['caption_mode']} margin={args.margin} "
          f"items={res['n_items']} icons={res['n_icons']}")
    print(f"eligible: strict={res['n_eligible_strict']} lenient={res['n_eligible_lenient']}")
    if not args.margin:
        print("\ntau   | STRICT prec/fp/cov           | LENIENT prec/fp/cov          | kappa")
        for s in sweep:
            ss, sl = s["strict"], s["lenient"]
            print(f"{s['tau']:.2f}  | {ss['precision_at_1']:.3f} {ss['fp_rate']:.3f} {ss['coverage']:.3f}        "
                  f"| {sl['precision_at_1']:.3f} {sl['fp_rate']:.3f} {sl['coverage']:.3f}       | {s['kappa']:.3f}")
    for label, op in (("STRICT", op_strict), ("LENIENT", op_lenient)):
        if op:
            o = op[label.lower()]
            print(f"\n{label} OPERATING POINT: tau={op['tau']} delta={op['delta']} "
                  f"prec@1={o['precision_at_1']} fp={o['fp_rate']} cov={o['coverage']} "
                  f"no_icon={o['no_icon_rate']} (kappa={op['kappa']})")
        else:
            print(f"\n{label}: no (tau,delta) satisfies fp<=0.05 & prec>=0.80")
    if slices:
        print("\n-- STRICT slices at the strict operating point --")
        for axis, d in slices.items():
            print(f"  [{axis}]")
            for v, m in d["strict"].items():
                print(f"    {v:>10}: prec={m['precision_at_1']:.3f} fp={m['fp_rate']:.3f} "
                      f"cov={m['coverage']:.3f} (shown={m['shown']}, tp={m['tp']}, fp={m['fp']})")
    print(f"\n[wrote {out}]")


if __name__ == "__main__":
    main()
