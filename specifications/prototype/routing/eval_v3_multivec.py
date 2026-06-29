"""Router improvement experiment: MULTI-VECTOR (centroid) icon captions.

Motivation (from the miss analysis): at tau=0.70, 90 of 132 misses are NEAR-MISSES
— the top icon is CORRECT but the asymmetric query->caption cosine peaks ~0.68-0.70
for genuine matches, so a flat tau can't separate "correct-just-below" from "wrong".

Fix tested here: represent each icon by the CENTROID of several short caption
*phrases* (the rich caption split into comma/space-delimited sense units, plus the
bare concept words), instead of one long caption string. Averaging multiple short
phrasings is a standard asymmetric-retrieval trick: it pulls genuine matches up
(the query is short, and at least one phrase is short+on-topic) without pulling
off-topic queries up as much. Embeddings are L2-normalised, averaged, re-normalised.

We also test MAX-over-phrases (best single phrase) as an alternative to centroid.

Compares 3 representations on the master 354-set, dual-perspective:
   single  : current one-string rich caption (baseline)
   centroid: mean of per-phrase embeddings
   maxphrase: max cosine over per-phrase embeddings (query vs best phrase)

Run: python routing/eval_v3_multivec.py
Writes data/eval3_multivec.json + prints the operating points.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from router import make_embedder, load_catalog  # noqa: E402
from eval_v2 import load_master, classify, cohens_kappa  # noqa: E402

DATA = HERE.parent / "data"


def _norm(mat: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(mat, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return mat / n


def caption_phrases(icon: dict) -> list[str]:
    """Split the rich caption into short sense-phrases + add the concept words.
    e.g. 'rocket spaceship launch space travel ...' -> windows of 1-3 words +
    the concept tokens. Keeps each phrase short (asymmetric-friendly)."""
    rich = icon["rich_caption"]
    words = re.split(r"[\s,/]+", rich.strip())
    words = [w for w in words if w]
    phrases = set()
    # the bare concept (split on / and -)
    concept_words = re.split(r"[\s/_-]+", icon["concept"])
    phrases.add(" ".join(w for w in concept_words if w))
    # name caption
    phrases.add(icon["name_caption"])
    # single words
    for w in words:
        phrases.add(w)
    # sliding 2- and 3-grams to keep a little context
    for k in (2, 3):
        for i in range(0, max(0, len(words) - k + 1)):
            phrases.add(" ".join(words[i:i + k]))
    # also keep the full rich caption as one phrase (context anchor)
    phrases.add(rich)
    return sorted(p for p in phrases if p.strip())


def build_reps(emb, catalog):
    """Return dict of representation_name -> structure needed to score."""
    single = _norm(emb.embed([ic["rich_caption"] for ic in catalog]))  # [N,384]

    # per-icon phrase embeddings (ragged) + centroid
    phrase_lists = [caption_phrases(ic) for ic in catalog]
    flat, offsets = [], [0]
    for pl in phrase_lists:
        flat.extend(pl)
        offsets.append(len(flat))
    flat_vecs = _norm(emb.embed(flat))
    centroids = np.zeros((len(catalog), single.shape[1]), dtype=np.float32)
    phrase_blocks = []
    for i in range(len(catalog)):
        block = flat_vecs[offsets[i]:offsets[i + 1]]
        phrase_blocks.append(block)
        centroids[i] = block.mean(axis=0)
    centroids = _norm(centroids)
    return {"single": single, "centroid": centroids, "phrase_blocks": phrase_blocks}


def score(items, sims_best_idx, sims_best, concepts, tau, perspective):
    key = f"expected_{perspective}"
    tp = fp = miss = tn = 0
    dec = []
    for i, it in enumerate(items):
        show = sims_best[i] >= tau
        cls = classify(it[key], show, concepts[sims_best_idx[i]])
        dec.append(cls)
        tp += cls == "tp"; fp += cls == "fp"; miss += cls == "miss"; tn += cls == "tn"
    shown = tp + fp
    n_elig = sum(1 for it in items if it[key])
    return {
        "precision_at_1": round(tp / shown, 4) if shown else 0.0,
        "fp_rate": round(fp / len(items), 4),
        "coverage": round(tp / n_elig, 4) if n_elig else 0.0,
        "no_icon_rate": round((miss + tn) / len(items), 4),
        "tp": tp, "fp": fp, "shown": shown,
    }, dec


def best_op(reps_sims, items, concepts, rep_name, perspective):
    """Sweep tau, return max-coverage point with fp<=0.05 & prec>=0.80."""
    bi, allsims = reps_sims  # bi: [Q], allsims used to recompute best_sim only
    taus = [round(x, 2) for x in np.arange(0.40, 0.86, 0.01)]
    best = None
    for tau in taus:
        m, _ = score(items, bi, allsims, concepts, tau, perspective)
        if m["fp_rate"] <= 0.05 and m["precision_at_1"] >= 0.80:
            if best is None or m["coverage"] > best[1]["coverage"]:
                best = (tau, m)
    if best is None:
        # relax to fp<=0.05 only
        for tau in taus:
            m, _ = score(items, bi, allsims, concepts, tau, perspective)
            if m["fp_rate"] <= 0.05:
                if best is None or m["coverage"] > best[1]["coverage"]:
                    best = (tau, m)
    return best


def main() -> None:
    emb = make_embedder("local")
    catalog = load_catalog()
    items = load_master()
    concepts = [ic["concept"] for ic in catalog]
    qv = _norm(emb.embed([it["text"] for it in items]))
    reps = build_reps(emb, catalog)

    results = {}
    # single & centroid: simple matmul
    for name in ("single", "centroid"):
        sims = qv @ reps[name].T
        bi = sims.argmax(axis=1)
        bs = sims[np.arange(len(items)), bi]
        results[name] = {}
        for persp in ("strict", "lenient"):
            op = best_op((bi, bs), items, concepts, name, persp)
            results[name][persp] = {"tau": op[0], **op[1]} if op else None

    # maxphrase: for each query, best phrase per icon, then argmax over icons
    pb = reps["phrase_blocks"]
    maxsims = np.zeros((len(items), len(catalog)), dtype=np.float32)
    for j, block in enumerate(pb):
        maxsims[:, j] = (qv @ block.T).max(axis=1)
    bi = maxsims.argmax(axis=1)
    bs = maxsims[np.arange(len(items)), bi]
    results["maxphrase"] = {}
    for persp in ("strict", "lenient"):
        op = best_op((bi, bs), items, concepts, "maxphrase", persp)
        results["maxphrase"][persp] = {"tau": op[0], **op[1]} if op else None

    (DATA / "eval3_multivec.json").write_text(json.dumps({
        "backend": emb.name, "n_items": len(items),
        "n_phrases_total": sum(len(b) for b in pb),
        "results": results,
    }, indent=2), encoding="utf-8")

    print(f"backend={emb.name}  items={len(items)}  "
          f"avg phrases/icon={sum(len(b) for b in pb)/len(pb):.1f}")
    print("\nrep         | persp   | tau  | prec@1 | fp    | coverage")
    for name in ("single", "centroid", "maxphrase"):
        for persp in ("strict", "lenient"):
            r = results[name][persp]
            if r:
                print(f"{name:11} | {persp:7} | {r['tau']:.2f} | {r['precision_at_1']:.3f}  | "
                      f"{r['fp_rate']:.3f} | {r['coverage']:.3f}")
    print("\n[wrote data/eval3_multivec.json]")


if __name__ == "__main__":
    main()
