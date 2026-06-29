"""Router improvement experiment: BGE asymmetric query instruction prefix.

The bge-small-en-v1.5 model card recommends prepending a retrieval instruction to
the QUERY side (not the document side) for short-query retrieval:
  "Represent this sentence for searching relevant passages: <query>"
This is the model's intended asymmetric-retrieval mode and directly targets the
skeptic's "symmetric model used for asymmetric query->caption" critique.

We test the official prefix and a domain-tuned variant, querying against the SAME
single rich-caption document embeddings (documents are NOT prefixed, per the card).
Dual-perspective, master 354-set.

Run: python routing/eval_v4_prefix.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from router import make_embedder, load_catalog  # noqa: E402
from eval_v2 import load_master, classify  # noqa: E402

DATA = HERE.parent / "data"

PREFIXES = {
    "none": "",
    "bge_official": "Represent this sentence for searching relevant passages: ",
    "domain": "Represent this quiz text to find a matching pictogram icon: ",
}


def _norm(mat):
    n = np.linalg.norm(mat, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return mat / n


def score(items, bi, bs, concepts, tau, persp):
    key = f"expected_{persp}"
    tp = fp = miss = tn = 0
    for i, it in enumerate(items):
        cls = classify(it[key], bs[i] >= tau, concepts[bi[i]])
        tp += cls == "tp"; fp += cls == "fp"; miss += cls == "miss"; tn += cls == "tn"
    shown = tp + fp
    n_elig = sum(1 for it in items if it[key])
    return {
        "precision_at_1": round(tp / shown, 4) if shown else 0.0,
        "fp_rate": round(fp / len(items), 4),
        "coverage": round(tp / n_elig, 4) if n_elig else 0.0,
    }


def best_op(items, bi, bs, concepts, persp):
    best = None
    for tau in [round(x, 2) for x in np.arange(0.30, 0.90, 0.01)]:
        m = score(items, bi, bs, concepts, tau, persp)
        if m["fp_rate"] <= 0.05 and m["precision_at_1"] >= 0.80:
            if best is None or m["coverage"] > best[1]["coverage"]:
                best = (tau, m)
    return best


def main():
    emb = make_embedder("local")
    catalog = load_catalog()
    items = load_master()
    concepts = [ic["concept"] for ic in catalog]
    cap_vecs = _norm(emb.embed([ic["rich_caption"] for ic in catalog]))  # docs: no prefix
    texts = [it["text"] for it in items]

    results = {}
    for name, pfx in PREFIXES.items():
        qv = _norm(emb.embed([pfx + t for t in texts]))
        sims = qv @ cap_vecs.T
        bi = sims.argmax(axis=1)
        bs = sims[np.arange(len(items)), bi]
        results[name] = {}
        for persp in ("strict", "lenient"):
            op = best_op(items, bi, bs, concepts, persp)
            results[name][persp] = {"tau": op[0], **op[1]} if op else None

    (DATA / "eval4_prefix.json").write_text(
        json.dumps({"backend": emb.name, "results": results}, indent=2), encoding="utf-8")
    print(f"backend={emb.name}  items={len(items)}")
    print("\nprefix        | persp   | tau  | prec@1 | fp    | coverage")
    for name in PREFIXES:
        for persp in ("strict", "lenient"):
            r = results[name][persp]
            if r:
                print(f"{name:13} | {persp:7} | {r['tau']:.2f} | {r['precision_at_1']:.3f}  | "
                      f"{r['fp_rate']:.3f} | {r['coverage']:.3f}")
    print("\n[wrote data/eval4_prefix.json]")


if __name__ == "__main__":
    main()
