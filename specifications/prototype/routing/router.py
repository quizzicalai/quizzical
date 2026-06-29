"""Q&A -> icon semantic nearest-neighbour router (prototype).

Mirrors the repo's `app/services/precompute/lookup.py::_vector_nn` pattern:
embed the query, cosine-NN against a candidate embedding matrix, apply a
relevance threshold tau, else return NO ICON. 384-dim to match the repo's
`Vector(384)` columns and `EmbeddingsCache(dim=384)`.

Two embedding backends (criterion 3 asks for >= 2 approaches):
  * local  -> fastembed BAAI/bge-small-en-v1.5 (384-dim, $0, offline)
  * openai -> text-embedding-3-small truncated/normalised to 384 dims
              (set OPENAI_API_KEY in the env; never logged)

Usage:
  python router.py eval   --backend local            # routing relevance + tau sweep
  python router.py eval   --backend openai
  python router.py scale  --backend local            # NN timing at 1k vs 10k+
  python router.py bind   --backend local --tau 0.62 # emit icon bindings for the FE demo
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
PROTO = HERE.parent
DATA = PROTO / "data"


# --------------------------------------------------------------------------
# Embedding backends
# --------------------------------------------------------------------------

_LOCAL_MODEL = "BAAI/bge-small-en-v1.5"  # 384-dim, matches repo Vector(384)
_OPENAI_MODEL = "text-embedding-3-small"  # 1536-dim native; we slice to 384


def _normalise(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


class LocalEmbedder:
    """fastembed bge-small-en-v1.5 -> 384-dim, L2-normalised."""

    dim = 384
    name = f"local:{_LOCAL_MODEL}"

    def __init__(self) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model_name=_LOCAL_MODEL)

    def embed(self, texts: list[str]) -> np.ndarray:
        vecs = list(self._model.embed(texts))
        return _normalise(np.asarray(vecs, dtype=np.float32))


class OpenAIEmbedder:
    """OpenAI text-embedding-3-small. Native 1536-dim; we request `dimensions=384`
    via the API (the model is Matryoshka-trained, so a 384-slice is valid) so it
    drops straight into the repo's Vector(384) cosine space. Key comes from env
    OPENAI_API_KEY and is NEVER printed/logged."""

    dim = 384
    name = f"openai:{_OPENAI_MODEL}@384"

    def __init__(self) -> None:
        from openai import OpenAI

        if not os.environ.get("OPENAI_API_KEY"):
            raise SystemExit("OPENAI_API_KEY not set in env (do not paste it on the CLI).")
        self._client = OpenAI()

    def embed(self, texts: list[str]) -> np.ndarray:
        out: list[list[float]] = []
        # batch to stay well under token limits
        for i in range(0, len(texts), 256):
            chunk = texts[i : i + 256]
            resp = self._client.embeddings.create(
                model=_OPENAI_MODEL, input=chunk, dimensions=384
            )
            out.extend(d.embedding for d in resp.data)
        return _normalise(np.asarray(out, dtype=np.float32))


def make_embedder(backend: str):
    if backend == "local":
        return LocalEmbedder()
    if backend == "openai":
        return OpenAIEmbedder()
    raise SystemExit(f"unknown backend {backend!r}")


# --------------------------------------------------------------------------
# Catalog + data
# --------------------------------------------------------------------------


def load_catalog() -> list[dict]:
    return json.loads((DATA / "icon_catalog.json").read_text(encoding="utf-8"))["icons"]


def load_qa() -> list[dict]:
    return json.loads((DATA / "qa_labeled.json").read_text(encoding="utf-8"))["items"]


def caption_for(icon: dict, mode: str) -> str:
    return icon["rich_caption"] if mode == "rich" else icon["name_caption"]


# --------------------------------------------------------------------------
# Routing core (mirrors lookup.py::_vector_nn — cosine NN + tau cutoff)
# --------------------------------------------------------------------------


def route_all(query_vecs: np.ndarray, cap_vecs: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return (best_idx, best_sim) per query. Vectors are L2-normalised, so a
    single matmul gives cosine similarity for every (query, caption) pair."""
    sims = query_vecs @ cap_vecs.T  # [Q, C] cosine
    best_idx = sims.argmax(axis=1)
    best_sim = sims[np.arange(sims.shape[0]), best_idx]
    return best_idx, best_sim


# --------------------------------------------------------------------------
# Evaluation: precision@1 / FP rate / no-icon rate, with a tau sweep
# --------------------------------------------------------------------------


def evaluate(backend: str, caption_mode: str) -> dict:
    emb = make_embedder(backend)
    catalog = load_catalog()
    qa = load_qa()

    concepts = [ic["concept"] for ic in catalog]
    captions = [caption_for(ic, caption_mode) for ic in catalog]

    cap_vecs = emb.embed(captions)
    q_texts = [item["text"] for item in qa]
    q_vecs = emb.embed(q_texts)

    best_idx, best_sim = route_all(q_vecs, cap_vecs)

    # Per-item top match (independent of tau) for transparency.
    rows = []
    for i, item in enumerate(qa):
        rows.append(
            {
                "text": item["text"],
                "kind": item["kind"],
                "expected": item["expected"],
                "top_concept": concepts[best_idx[i]],
                "top_sim": round(float(best_sim[i]), 4),
            }
        )

    # tau sweep: at each tau, decide show/no-show, then classify.
    #   - item with expected==[]  -> correct outcome is NO ICON.
    #       shown -> false positive; not shown -> correct (true negative).
    #   - item with expected!=[]  -> icon-eligible.
    #       shown & top in expected -> true positive (precision@1 hit)
    #       shown & top not expected -> false positive (wrong icon)
    #       not shown -> a miss (coverage loss), NOT a false positive.
    taus = [round(x, 2) for x in np.arange(0.40, 0.86, 0.02)]
    sweep = []
    n_eligible = sum(1 for it in qa if it["expected"])
    n_noicon = sum(1 for it in qa if not it["expected"])
    for tau in taus:
        tp = fp_wrong = fp_should_be_none = shown = covered_eligible = 0
        for i, item in enumerate(qa):
            show = best_sim[i] >= tau
            if show:
                shown += 1
            if item["expected"]:  # icon-eligible
                if show:
                    if concepts[best_idx[i]] in item["expected"]:
                        tp += 1
                        covered_eligible += 1
                    else:
                        fp_wrong += 1
            else:  # should be NO icon
                if show:
                    fp_should_be_none += 1
        # precision@1 among SHOWN icons (the user-visible quality bar)
        total_shown = tp + fp_wrong + fp_should_be_none
        precision = tp / total_shown if total_shown else 0.0
        # false-positive rate = fraction of ALL items where a WRONG/uncalled icon shows
        fp_rate = (fp_wrong + fp_should_be_none) / len(qa)
        # coverage = eligible items that got a correct icon / all eligible
        coverage = covered_eligible / n_eligible if n_eligible else 0.0
        # no-icon rate = items shown nothing / all
        no_icon_rate = (len(qa) - shown) / len(qa)
        sweep.append(
            {
                "tau": tau,
                "precision_at_1": round(precision, 4),
                "fp_rate": round(fp_rate, 4),
                "coverage": round(coverage, 4),
                "no_icon_rate": round(no_icon_rate, 4),
                "tp": tp,
                "fp_wrong_icon": fp_wrong,
                "fp_on_abstract": fp_should_be_none,
                "shown": shown,
            }
        )

    return {
        "backend": emb.name,
        "caption_mode": caption_mode,
        "n_items": len(qa),
        "n_eligible": n_eligible,
        "n_no_icon_expected": n_noicon,
        "n_icons": len(catalog),
        "per_item": rows,
        "tau_sweep": sweep,
    }


def pick_operating_point(sweep: list[dict]) -> dict | None:
    """Pick the tau that maximises coverage subject to fp_rate <= 0.05 and
    precision@1 >= 0.80 (the plan's R2 bar). Fall back to the lowest-FP point."""
    feasible = [s for s in sweep if s["fp_rate"] <= 0.05 and s["precision_at_1"] >= 0.80]
    if feasible:
        return max(feasible, key=lambda s: s["coverage"])
    # relax: just satisfy FP<=0.05, maximise coverage
    fp_ok = [s for s in sweep if s["fp_rate"] <= 0.05]
    if fp_ok:
        return max(fp_ok, key=lambda s: s["coverage"])
    return None


# --------------------------------------------------------------------------
# Scalability: NN timing at 1k vs 10k+ captions
# --------------------------------------------------------------------------


def scale(backend: str) -> dict:
    emb = make_embedder(backend)
    catalog = load_catalog()
    captions = [caption_for(ic, "rich") for ic in catalog]
    base = emb.embed(captions)  # [N0, 384]
    rng = np.random.default_rng(7)

    qa = load_qa()
    q_vecs = emb.embed([it["text"] for it in qa])  # realistic query batch

    # Warm up numpy/BLAS so the first sized run isn't penalised by lazy init.
    _ = route_all(q_vecs, _normalise(rng.normal(0, 1, (2_000, base.shape[1])).astype(np.float32)))

    results = []
    for n in [1_000, 5_000, 10_000, 50_000, 100_000]:
        # Pad the real catalog out to N synthetic captions by jittering real
        # vectors (keeps them on the unit sphere; only the NN *timing* matters
        # here, which is governed by N x dim, not the synthetic content).
        reps = int(np.ceil(n / base.shape[0]))
        big = np.repeat(base, reps, axis=0)[:n].copy()
        big += rng.normal(0, 0.01, big.shape).astype(np.float32)
        big = _normalise(big)

        # Brute-force matmul NN (the SQLite/in-Python path in lookup.py). On
        # Postgres this is an IVFFlat ANN index query, which is sub-linear and
        # strictly faster; brute force is the conservative upper bound.
        # Take the MIN of many reps: the min reflects the cost of the work
        # itself, with OS/GC/BLAS-spin-up noise pushed into the slower reps.
        _ = route_all(q_vecs, big)  # per-size warmup
        samples = []
        for _ in range(20):
            t0 = time.perf_counter()
            _ = route_all(q_vecs, big)
            samples.append(time.perf_counter() - t0)
        dt = min(samples)
        per_query_us = dt / q_vecs.shape[0] * 1e6
        results.append(
            {
                "n_icons": n,
                "batch_queries": int(q_vecs.shape[0]),
                "batch_ms": round(dt * 1e3, 3),
                "per_query_us": round(per_query_us, 2),
            }
        )
    return {"backend": emb.name, "method": "brute-force cosine (upper bound vs IVFFlat ANN)", "runs": results}


# --------------------------------------------------------------------------
# Bind: emit { text -> icon } for the FE demo (precomputed, like the pack path)
# --------------------------------------------------------------------------


def bind(backend: str, tau: float, caption_mode: str = "rich") -> dict:
    emb = make_embedder(backend)
    catalog = load_catalog()
    qa = load_qa()
    captions = [caption_for(ic, caption_mode) for ic in catalog]
    cap_vecs = emb.embed(captions)
    q_vecs = emb.embed([it["text"] for it in qa])
    best_idx, best_sim = route_all(q_vecs, cap_vecs)
    out = []
    for i, item in enumerate(qa):
        chosen = catalog[best_idx[i]] if best_sim[i] >= tau else None
        out.append(
            {
                "text": item["text"],
                "kind": item["kind"],
                "sim": round(float(best_sim[i]), 4),
                "iconId": chosen["id"] if chosen else None,
                "lucide": chosen["lucide"] if chosen else None,
                "concept": chosen["concept"] if chosen else None,
            }
        )
    return {"backend": emb.name, "tau": tau, "caption_mode": caption_mode, "bindings": out}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["eval", "scale", "bind"])
    p.add_argument("--backend", default="local", choices=["local", "openai"])
    p.add_argument("--captions", default="rich", choices=["rich", "name"])
    p.add_argument("--tau", type=float, default=0.62)
    p.add_argument("--out", default="")
    args = p.parse_args()

    if args.cmd == "eval":
        res = evaluate(args.backend, args.captions)
        op = pick_operating_point(res["tau_sweep"])
        res["operating_point"] = op
        text = json.dumps(res, indent=2)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
        # human summary
        print(f"backend={res['backend']} captions={res['caption_mode']} "
              f"items={res['n_items']} eligible={res['n_eligible']} "
              f"no-icon-expected={res['n_no_icon_expected']} icons={res['n_icons']}")
        print("tau   prec@1  fp_rate  coverage  no_icon")
        for s in res["tau_sweep"]:
            print(f"{s['tau']:.2f}  {s['precision_at_1']:.3f}   {s['fp_rate']:.3f}    "
                  f"{s['coverage']:.3f}     {s['no_icon_rate']:.3f}")
        if op:
            print(f"\nOPERATING POINT: tau={op['tau']} precision@1={op['precision_at_1']} "
                  f"fp_rate={op['fp_rate']} coverage={op['coverage']} no_icon={op['no_icon_rate']}")
        else:
            print("\nNo tau satisfies fp_rate<=0.05 — feature would ship low-coverage by design.")
        if args.out:
            print(f"[wrote {args.out}]")

    elif args.cmd == "scale":
        res = scale(args.backend)
        text = json.dumps(res, indent=2)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
        print(f"backend={res['backend']} ({res['method']})")
        print("n_icons   batch_ms  per_query_us")
        for r in res["runs"]:
            print(f"{r['n_icons']:>7}   {r['batch_ms']:>7}   {r['per_query_us']:>9}")
        if args.out:
            print(f"[wrote {args.out}]")

    elif args.cmd == "bind":
        res = bind(args.backend, args.tau, args.captions)
        text = json.dumps(res, indent=2)
        out = args.out or str(PROTO / "data" / "bindings.json")
        Path(out).write_text(text, encoding="utf-8")
        n_bound = sum(1 for b in res["bindings"] if b["iconId"])
        print(f"bound {n_bound}/{len(res['bindings'])} items at tau={args.tau} -> {out}")


if __name__ == "__main__":
    main()
