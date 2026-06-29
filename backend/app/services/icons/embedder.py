"""Local 384-dim embedder for the Q&A icon enrichment pipeline (DRAFT).

This is the real, de-stubbed embedder the prototype proved out
(``prototype/qa-image-enrichment``: ``pipeline/embed_fn.py``). It produces
384-dim, L2-normalised vectors with ``BAAI/bge-small-en-v1.5`` (the model the
routing eval validated), in the SAME embedding space as the repo's
``Vector(384)`` columns (``topics.embedding``, ``embeddings_cache.embedding``,
``icon_assets.embedding``).

LAZINESS (the #1 hard requirement):
  - ``fastembed`` is imported **inside** ``_get_model`` only — importing THIS
    module does not import fastembed and does not load the model.
  - This module is itself imported only on the flag-ON path (see
    ``app.services.icons.hook.maybe_bind_icons``). When the flag is off, neither
    this module nor fastembed is touched, and no model is loaded.

Contract:
  - ``raw_embed`` matches ``app.services.precompute.lookup.EmbedFn``
    (``Callable[[str], Awaitable[list[float] | None]]``): empty/blank text -> None.
  - ``embed_one`` matches ``app.services.embeddings.cache.EmbedFn``
    (``Callable[[str], Awaitable[list[float]]]``) so it can be wired through
    ``get_or_compute_embedding`` for embed-once-ever caching.

The CPU-bound embed is bridged off the event loop via ``run_in_executor`` so it
never blocks FastAPI's async stack — the same pattern used to bridge a sync
sentence-transformer into async code.
"""

from __future__ import annotations

import asyncio
import functools
import threading

MODEL_NAME = "BAAI/bge-small-en-v1.5"
DIM = 384

_model_lock = threading.Lock()
_model = None


def _get_model():
    """Lazily construct (and memoise) the fastembed model. fastembed is imported
    here, not at module import time, so nothing is loaded until first use."""
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from fastembed import TextEmbedding  # lazy, heavy

                _model = TextEmbedding(model_name=MODEL_NAME)
    return _model


def _normalize(vec) -> list[float]:
    """L2-normalise an iterable of floats into a plain ``list[float]``."""
    import numpy as np  # lazy: only needed on the flag-ON path

    arr = np.asarray(vec, dtype=np.float32)
    n = float(np.linalg.norm(arr))
    if n:
        arr = arr / n
    if arr.shape[0] != DIM:
        raise ValueError(f"expected {DIM} dims, got {arr.shape[0]}")
    return arr.astype(float).tolist()


def embed_one_sync(text: str) -> list[float]:
    """Synchronous primitive: text -> 384-dim L2-normalised list[float]."""
    model = _get_model()
    vec = next(iter(model.embed([text])))
    return _normalize(vec)


def embed_many_sync(texts: list[str]) -> list[list[float]]:
    """Batched primitive (much faster than calling ``embed_one_sync`` in a loop)
    — used by the offline icon-index build / seed path."""
    model = _get_model()
    return [_normalize(vec) for vec in model.embed(texts)]


@functools.lru_cache(maxsize=8192)
def _cached(text: str) -> tuple[float, ...]:
    return tuple(embed_one_sync(text))


async def embed_one(text: str) -> list[float]:
    """Async, non-None primitive matching ``embeddings.cache.EmbedFn``.

    Runs the CPU-bound embed off the event loop. Raises ``ValueError`` on empty
    text (the caller — ``raw_embed`` — guards before calling)."""
    if not text or not text.strip():
        raise ValueError("embed_one received empty text")
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: list(_cached(text)))


async def raw_embed(text: str) -> list[float] | None:
    """Async ``EmbedFn``-compatible primitive matching ``lookup.EmbedFn``.

    Empty/blank text -> None (graceful), exactly like the prototype + the topic
    NN path, so the binder mirrors ``_vector_nn`` 1:1."""
    if not text or not text.strip():
        return None
    return await embed_one(text)
