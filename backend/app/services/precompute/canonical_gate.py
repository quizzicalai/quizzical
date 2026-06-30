"""Blend-aware canonical correctness gate for §21 precompute persist.

The owner's invariant is "our canonical set only improves the more we add to
it": once a topic is in the reviewed canonical catalog, a precomputed pack for
that topic MUST resolve to the canonical outcome set. This module is the shared,
pure comparison core used by two callers:

- ``builder.run_build`` — BEFORE ``persist_fn``: a mismatch is routed to
  quarantine with reason ``canonical_mismatch`` (the artefact is NOT persisted).
- ``evaluator`` — the same assertion is surfaced as a hard ``is_blocked`` reason
  so a two-judge run can never promote a canonically-wrong set.

Blend awareness (the load-bearing rule)
---------------------------------------
``outcome_mode`` on a catalog set decides the comparison:

- ``single`` (MBTI, Hogwarts, Enneagram, …): the artefact's character set must
  EXACTLY equal the canonical set (order-independent, case/accent-folded). A
  missing, extra, or renamed outcome fails.
- ``blended`` (DISC, Big Five / OCEAN): the outcome is naturally a *profile*
  blended across the canonical dimensions. We therefore require the set to be
  drawn FROM the canonical PALETTE — every named outcome must be a canonical
  dimension — but we do NOT force exactly-one and do NOT force all-of-N. A DISC
  blended set (e.g. just ``Dominance`` + ``Influence``) PASSES; a wrong-NAMED
  set (``Director`` instead of ``Dominance``) FAILS. This is what lets the
  upcoming blended-DISC feature ship through the gate.

The module is pure / side-effect free (no DB, no LLM) and works identically in
SQLite tests and the live worker. Reject-to-quarantine only — NO auto-repair.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from app.agent.canonical_sets import (
    OUTCOME_MODE_BLENDED,
    OUTCOME_MODE_SINGLE,
    canonical_for,
    canonical_outcome_mode,
    canonical_title_for,
)
from app.services.precompute.canonicalize import canonical_key_for_name

CANONICAL_MISMATCH_REASON = "canonical_mismatch"


@dataclass(frozen=True)
class CanonicalCheck:
    """Result of comparing an artefact's character set to canonical.

    ``ok`` is True when there is nothing to enforce (non-canonical topic) OR the
    set is canonically correct for the topic's ``outcome_mode``. ``is_canonical``
    distinguishes "no canonical set exists" (skip) from "canonical set exists and
    matched/mismatched".
    """

    ok: bool
    is_canonical: bool
    outcome_mode: str | None
    title: str | None
    diff: str  # human-readable diff; "" when ok or non-canonical


def _names_from_artefact(artefact: Any) -> list[str]:
    """Extract the outcome/character names from a precompute artefact.

    Tolerant of the generator-agnostic artefact shape (mirrors the icon hook):
    accepts a mapping or an object, and a ``characters`` list of dicts with a
    ``name`` (the build artefact shape) OR a plain list of strings. Also accepts
    the stored ``character_set`` snapshot shape used by the on-demand evaluator.
    """
    obj: Any = artefact
    if not isinstance(obj, Mapping):
        obj = getattr(artefact, "__dict__", None) or {}
    chars = (
        obj.get("characters")
        or obj.get("character_set")
        or obj.get("archetypes")
        or obj.get("ideal_archetypes")
        or []
    )
    return _coerce_names(chars)


def _coerce_names(chars: Any) -> list[str]:
    out: list[str] = []
    if not isinstance(chars, Iterable) or isinstance(chars, (str, bytes)):
        return out
    for c in chars:
        if isinstance(c, str):
            name = c
        elif isinstance(c, Mapping):
            name = c.get("name") or c.get("display_name") or ""
        else:
            name = getattr(c, "name", "") or ""
        name = str(name).strip()
        if name:
            out.append(name)
    return out


def _keyset(names: Iterable[str]) -> set[str]:
    return {canonical_key_for_name(n) for n in names if canonical_key_for_name(n)}


def compare_sets(
    canonical_names: list[str],
    actual_names: list[str],
    *,
    outcome_mode: str,
) -> tuple[bool, str]:
    """Compare ``actual_names`` against ``canonical_names`` for ``outcome_mode``.

    Returns ``(ok, diff)``. The diff is empty on a pass.
    """
    canon_keys = _keyset(canonical_names)
    actual_keys = _keyset(actual_names)

    if not actual_keys:
        return False, "empty_outcome_set"

    if outcome_mode == OUTCOME_MODE_BLENDED:
        # Palette-consistent: every outcome must be drawn from the canonical
        # palette. Do NOT force exactly-one and do NOT force all-of-N.
        off_palette = sorted(
            n for n in actual_names if canonical_key_for_name(n) not in canon_keys
        )
        if off_palette:
            return False, f"off_palette={off_palette}; palette={sorted(canonical_names)}"
        return True, ""

    # single: exact set match (order-independent).
    missing = canon_keys - actual_keys
    extra = actual_keys - canon_keys
    if missing or extra:
        # Render using the original-cased names where possible for readability.
        missing_names = sorted(
            n for n in canonical_names if canonical_key_for_name(n) in missing
        )
        extra_names = sorted(
            n for n in actual_names if canonical_key_for_name(n) in extra
        )
        return False, f"missing={missing_names}; extra={extra_names}"

    # The unique-key sets matched, but a duplicated outcome name would collapse
    # in the set comparison above and slip through (two identical outcome cards
    # for a single-pick quiz). Flag it: the de-duplicated keys equal canonical,
    # so any surplus of raw names over unique keys is a repeat.
    actual_name_keys = [canonical_key_for_name(n) for n in actual_names]
    actual_name_keys = [k for k in actual_name_keys if k]
    if len(actual_name_keys) > len(actual_keys):
        from collections import Counter  # noqa: PLC0415

        dups = sorted(k for k, c in Counter(actual_name_keys).items() if c > 1)
        return False, f"duplicate_outcome_names={dups}"
    return True, ""


def check_artefact(category: str | None, artefact: Any) -> CanonicalCheck:
    """Compare an artefact's outcome set against the canonical set for ``category``.

    Non-canonical topics (no ``canonical_for`` match) short-circuit to
    ``ok=True, is_canonical=False`` (the gate is a no-op for them — the LLM judge
    owns those, not this gate).
    """
    canon = canonical_for(category)
    if not canon:
        return CanonicalCheck(
            ok=True, is_canonical=False, outcome_mode=None, title=None, diff=""
        )

    mode = canonical_outcome_mode(category) or OUTCOME_MODE_SINGLE
    title = canonical_title_for(category)
    actual = _names_from_artefact(artefact)
    ok, diff = compare_sets(canon, actual, outcome_mode=mode)
    return CanonicalCheck(
        ok=ok, is_canonical=True, outcome_mode=mode, title=title, diff=diff
    )


def topic_category(topic: Any) -> str | None:
    """Best-effort pull of the user-facing category string off a Topic-like object.

    The precompute ``Topic`` carries ``display_name`` / ``slug``; the canonical
    lookup is noise-tolerant so either works as the lookup key.
    """
    for attr in ("display_name", "category", "title", "slug"):
        val = getattr(topic, attr, None)
        if isinstance(val, str) and val.strip():
            return val
    if isinstance(topic, Mapping):
        for key in ("display_name", "category", "title", "slug"):
            val = topic.get(key)
            if isinstance(val, str) and val.strip():
                return val
    return None


__all__ = [
    "CANONICAL_MISMATCH_REASON",
    "CanonicalCheck",
    "OUTCOME_MODE_BLENDED",
    "OUTCOME_MODE_SINGLE",
    "check_artefact",
    "compare_sets",
    "topic_category",
]
