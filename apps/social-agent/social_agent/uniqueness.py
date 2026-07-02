"""Uniqueness gate: exact-match + semantic (cosine) dedup. Stdlib-only.

The owner's rule: NEVER repeat same-or-similar language, enforced against ALL
past posts and replies. Two layers:

1. Exact: `textutils.normalize_for_dedup` canonical form, compared as a set
   (also enforced by a partial UNIQUE index in Postgres as the last line of
   defense against races).
2. Semantic: cosine similarity of embeddings; a candidate whose similarity to
   ANY existing non-rejected post exceeds ``SEMANTIC_DUP_THRESHOLD`` is
   rejected.
"""
from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass, field

# Candidates closer than this to any existing post are considered "similar
# language" and rejected (owner requirement: reject > 0.85 cosine).
SEMANTIC_DUP_THRESHOLD = 0.85

EMBED_DIM = 384  # matches VECTOR(384) used across the quizzical schema


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        raise ValueError("cosine: dimension mismatch or empty vectors")
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def hash_embedding(text: str, dim: int = EMBED_DIM) -> list[float]:
    """Deterministic offline fallback embedding (character n-gram hashing).

    Not as good as a real model but catches heavy word overlap, keeps the
    pipeline working with no network, and keeps tests hermetic. L2-normalized.
    """
    vec = [0.0] * dim
    words = text.split()
    grams: list[str] = []
    grams.extend(words)
    grams.extend(" ".join(p) for p in zip(words, words[1:]))
    padded = f"  {text}  "
    grams.extend(padded[i : i + 3] for i in range(len(padded) - 2))
    for g in grams:
        h = hashlib.blake2b(g.encode("utf-8"), digest_size=8).digest()
        (idx,) = struct.unpack(">Q", h)
        sign = 1.0 if idx & 1 else -1.0
        vec[(idx >> 1) % dim] += sign
    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0:
        vec = [x / norm for x in vec]
    return vec


@dataclass
class UniquenessResult:
    unique: bool
    reason: str = ""
    max_similarity: float = 0.0
    nearest_text: str = ""


@dataclass
class UniquenessGate:
    """In-memory gate over the full history (loaded from PG at cycle start).

    ``existing_norms``: set of normalized texts of all non-rejected rows.
    ``existing_embeddings``: list of (embedding, original_text).
    """

    existing_norms: set[str] = field(default_factory=set)
    existing_embeddings: list[tuple[list[float], str]] = field(default_factory=list)
    threshold: float = SEMANTIC_DUP_THRESHOLD

    def check(self, norm_text: str, embedding: list[float] | None) -> UniquenessResult:
        if not norm_text:
            return UniquenessResult(False, reason="empty text after normalization")
        if norm_text in self.existing_norms:
            return UniquenessResult(False, reason="exact duplicate", max_similarity=1.0)
        best = 0.0
        best_text = ""
        if embedding is not None:
            for emb, original in self.existing_embeddings:
                if len(emb) != len(embedding):
                    continue  # skip cross-model/dimension rows
                sim = cosine(embedding, emb)
                if sim > best:
                    best, best_text = sim, original
                    if best > 0.999:
                        break
            if best > self.threshold:
                return UniquenessResult(
                    False,
                    reason=f"semantic duplicate (cosine {best:.3f} > {self.threshold})",
                    max_similarity=best,
                    nearest_text=best_text,
                )
        return UniquenessResult(True, max_similarity=best, nearest_text=best_text)

    def admit(self, norm_text: str, embedding: list[float] | None, original: str) -> None:
        """Register an accepted candidate so later candidates dedup against it."""
        self.existing_norms.add(norm_text)
        if embedding is not None:
            self.existing_embeddings.append((embedding, original))
