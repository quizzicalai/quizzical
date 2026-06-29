"""De-stubbed 384-dim embed_fn — the concrete embedder the repo's
`api/dependencies.py::get_precompute_lookup` wires as `embed_fn=None` today.

This is a REAL, runnable embedder (not a stub) that matches the repo's contracts:

  * Signature matches `app.services.embeddings.cache.EmbedFn`
        EmbedFn = Callable[[str], Awaitable[list[float]]]
    and `app.services.precompute.lookup.EmbedFn`
        EmbedFn = Callable[[str], Awaitable[list[float] | None]]
  * Output is 384-dim, L2-normalised float list -> drops straight into the
    repo's `Vector(384)` columns (topics.embedding, characters.embedding,
    embeddings_cache.embedding, and the proposed icon_assets.embedding).
  * Model = BAAI/bge-small-en-v1.5 (the model the routing eval validated).

PRODUCTION WIRING (the 3-line change this prototype proves out):

    # backend/app/api/dependencies.py  (replace embed_fn=None)
    from app.services.embeddings.local_embedder import get_embed_fn  # new module
    return PrecomputeLookup(..., embed_fn=get_embed_fn(db_session))

where get_embed_fn closes over the request db session and routes through
get_or_compute_embedding so every embedding is cached once, ever (AC-PRECOMP-COST-1):

    def get_embed_fn(session):
        async def _embed(text: str) -> list[float] | None:
            return await get_or_compute_embedding(
                session, text,
                model="BAAI/bge-small-en-v1.5", dim=384,
                embed_fn=_raw_embed,   # the function below
            )
        return _embed

This file ships `_raw_embed` (the uncached primitive) + a tiny in-proc LRU so the
prototype can run the binder without a DB. Swapping the LRU for the DB-backed
embeddings_cache is the only delta to production.
"""
from __future__ import annotations

import asyncio
import functools
import threading

import numpy as np

MODEL_NAME = "BAAI/bge-small-en-v1.5"
DIM = 384

_model_lock = threading.Lock()
_model = None


def _get_model():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from fastembed import TextEmbedding

                _model = TextEmbedding(model_name=MODEL_NAME)
    return _model


def embed_one_sync(text: str) -> list[float]:
    """Synchronous primitive: text -> 384-dim L2-normalised list[float]."""
    model = _get_model()
    vec = next(iter(model.embed([text])))
    arr = np.asarray(vec, dtype=np.float32)
    n = float(np.linalg.norm(arr))
    if n:
        arr = arr / n
    assert arr.shape[0] == DIM, f"expected {DIM} dims, got {arr.shape[0]}"
    return arr.astype(float).tolist()


def embed_many_sync(texts: list[str]) -> list[list[float]]:
    """Batched primitive (much faster than calling embed_one_sync in a loop)."""
    model = _get_model()
    out = []
    for vec in model.embed(texts):
        arr = np.asarray(vec, dtype=np.float32)
        n = float(np.linalg.norm(arr))
        if n:
            arr = arr / n
        out.append(arr.astype(float).tolist())
    return out


@functools.lru_cache(maxsize=8192)
def _cached(text: str) -> tuple[float, ...]:
    return tuple(embed_one_sync(text))


async def raw_embed(text: str) -> list[float] | None:
    """Async EmbedFn-compatible primitive (uncached at the DB layer; an in-proc
    LRU stands in for embeddings_cache so the prototype runs without Postgres).

    Runs the CPU-bound embed off the event loop via run_in_executor, matching how
    a sync sentence-transformer is correctly bridged into FastAPI's async stack."""
    if not text or not text.strip():
        return None
    loop = asyncio.get_running_loop()
    vec = await loop.run_in_executor(None, lambda: list(_cached(text)))
    return vec


if __name__ == "__main__":
    # smoke test: prove it emits 384 dims, unit-norm, and is deterministic.
    import math

    a = embed_one_sync("a rocket launches into orbit")
    b = embed_one_sync("a rocket launches into orbit")
    print("dim:", len(a))
    print("unit-norm:", round(math.sqrt(sum(x * x for x in a)), 6))
    print("deterministic:", a == b)
    c = embed_one_sync("a fresh crisp apple")
    cos = sum(x * y for x, y in zip(a, c))
    print("cos(rocket, apple):", round(cos, 4), "(should be low)")
