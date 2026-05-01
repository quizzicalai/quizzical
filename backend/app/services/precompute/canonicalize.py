"""
Canonicalisation helper for §21 precompute.

`canonical_key_for_name` produces the deterministic key used to dedupe
characters across topics (`AC-PRECOMP-DEDUP-1`) and to drive alias matching.

Implementation notes:
- Lowercased, accent-stripped (NFKD normalisation), whitespace-collapsed.
- Pure-Python: does not depend on Postgres `unaccent` so the same key is
  produced under SQLite tests AND in the live worker.
- Deterministic and side-effect free; safe to call from anywhere.
"""

from __future__ import annotations

import re
import unicodedata

_WS_RE = re.compile(r"\s+")


def canonical_key_for_name(name: str) -> str:
    """Return the canonical dedup key for a character / topic name.

    >>> canonical_key_for_name("Foo Bar")
    'foo bar'
    >>> canonical_key_for_name("  HÉCTOR  ")
    'hector'
    """
    if name is None:
        return ""
    # NFKD splits accented letters into base + combining mark; we drop the
    # combining marks (category "Mn") to fold accents.
    decomposed = unicodedata.normalize("NFKD", str(name))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    collapsed = _WS_RE.sub(" ", stripped).strip().lower()
    return collapsed


__all__ = ["canonical_key_for_name"]
