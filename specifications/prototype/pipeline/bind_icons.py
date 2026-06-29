"""De-stubbed BUILD-TIME icon binder.

The prototype's FE rode on a static hand-made binding file. This module is the
real thing: it mirrors the repo's `precompute/lookup.py::_vector_nn` selection
EXACTLY (embed query -> cosine vs every candidate -> argmax -> tau cutoff -> else
None) but against the icon index instead of topics, and runs through the concrete
async `embed_fn`. This is the function that would live in `builder.py::run_build`
to populate `questions.image_asset_id` / answer-option icon bindings into the pack.

Key fidelity points:
  * Uses pipeline/embed_fn.py::raw_embed  (the 384-dim async EmbedFn).
  * Cosine via the SAME math as lookup.py::_default_cosine.
  * tau cutoff => below tau binds NOTHING (graceful no-icon), exactly like _vector_nn
    returning None below thresholds.match.
  * Async, batched-free (one embed per string) so it matches the per-string call
    shape the live fail-open background path would use; the offline pack build can
    batch via embed_many_sync for speed (shown in build_pack_bindings).

Run:
  python pipeline/bind_icons.py --tau 0.70                 # bind the eval master set
  python pipeline/bind_icons.py --tau 0.70 --demo          # bind a tiny inline pack & print
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
PROTO = HERE.parent
DATA = PROTO / "data"


def _cosine(a: list[float], b: list[float]) -> float:
    """Identical to lookup.py::_default_cosine (pure-python cosine)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / ((na ** 0.5) * (nb ** 0.5))


def load_index() -> list[dict]:
    p = DATA / "icon_index.json"
    if not p.exists():
        raise SystemExit("icon_index.json missing — run pipeline/build_icon_index.py first.")
    return json.loads(p.read_text(encoding="utf-8"))["icons"]


async def bind_one(text: str, index: list[dict], tau: float) -> dict | None:
    """Mirror of _vector_nn: embed, cosine-argmax over candidates, tau cutoff.
    Returns the chosen icon dict or None (graceful no-icon)."""
    from embed_fn import raw_embed

    q = await raw_embed(text)
    if not q:
        return None
    best = None
    for ic in index:
        sim = _cosine(q, ic["embedding"])
        if best is None or sim > best[1]:
            best = (ic, sim)
    if best is None or best[1] < tau:
        return None
    ic, sim = best
    return {"icon_id": ic["id"], "lucide": ic["lucide"], "concept": ic["concept"],
            "palette_variant": ic["palette_variant"], "similarity": round(sim, 4)}


def build_pack_bindings(strings: list[str], index: list[dict], tau: float) -> list[dict]:
    """Offline pack-build path: batch-embed all strings, cosine vs index matrix,
    argmax + tau. Numerically identical to bind_one but vectorised for the build."""
    from embed_fn import embed_many_sync

    qv = np.asarray(embed_many_sync(strings), dtype=np.float32)        # [S,384] unit-norm
    cv = np.asarray([ic["embedding"] for ic in index], dtype=np.float32)  # [N,384] unit-norm
    sims = qv @ cv.T
    bi = sims.argmax(axis=1)
    bs = sims[np.arange(len(strings)), bi]
    out = []
    for s, idx, sim in zip(strings, bi, bs):
        if sim >= tau:
            ic = index[int(idx)]
            out.append({"text": s, "icon_id": ic["id"], "lucide": ic["lucide"],
                        "concept": ic["concept"], "palette_variant": ic["palette_variant"],
                        "similarity": round(float(sim), 4)})
        else:
            out.append({"text": s, "icon_id": None, "similarity": round(float(sim), 4)})
    return out


async def _amain(tau: float, demo: bool) -> None:
    index = load_index()
    if demo:
        # a tiny realistic pack to show the binding shape a pack JSON would carry
        pack = {
            "question": "Which classic Kanto starter Pokemon are you?",
            "options": ["A grass type that grows stronger over time",
                        "A fire type with a burning tail",
                        "A water type that defends with its shell",
                        "Charmander"],
        }
        qb = await bind_one(pack["question"], index, tau)
        ob = [await bind_one(o, index, tau) for o in pack["options"]]
        print(json.dumps({"tau": tau, "question": {"text": pack["question"], "icon": qb},
                          "options": [{"text": t, "icon": b} for t, b in zip(pack["options"], ob)]},
                         indent=2))
        return
    # bind the whole master eval set via the vectorised build path
    items = json.loads((DATA / "qa_labeled_master.json").read_text(encoding="utf-8"))["items"]
    strings = [it["text"] for it in items]
    binds = build_pack_bindings(strings, index, tau)
    n = sum(1 for b in binds if b["icon_id"])
    (DATA / "pack_bindings_v2.json").write_text(
        json.dumps({"tau": tau, "model": "BAAI/bge-small-en-v1.5", "n_bound": n,
                    "n_total": len(binds), "bindings": binds}, indent=2), encoding="utf-8")
    print(f"bound {n}/{len(binds)} strings at tau={tau} -> data/pack_bindings_v2.json")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tau", type=float, default=0.70)
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()
    asyncio.run(_amain(args.tau, args.demo))


if __name__ == "__main__":
    main()
