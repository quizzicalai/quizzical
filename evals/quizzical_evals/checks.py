"""Deterministic, code-only quality checks per agent function.

These are cheap, non-LLM gates that catch the failure modes we *don't* need a
judge for: malformed structure, count/length violations, leaked outcome names,
duplicate questions, missing paragraphs in the final reading, etc. They mirror
the hard guards the production code already enforces (see
``content_creation_tools.py`` ``_ensure_min_options`` / ``MIN_FINAL_PARAGRAPHS``
/ ``_dedupe_questions_by_text`` and ``schemas.py`` length floors), so a config
that "passes the judge" but trips a structural rule is still disqualified.

Each check takes the parsed output object (already schema-validated upstream)
and returns ``True`` on pass. They are referenced by name in the function
specs (``deterministic_checks`` / ``QualityFloor.required_checks``) and dispatched
through ``run_checks``. Keep them pure and import-light.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n+")

# Production floors copied from backend/app/agent/tools/content_creation_tools.py
MIN_FINAL_PARAGRAPHS = 3
MIN_FINAL_DESCRIPTION_CHARS = 400


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _count_paragraphs(text: str | None) -> int:
    if not text:
        return 0
    return len([b for b in _PARAGRAPH_SPLIT_RE.split(text) if b and b.strip()])


# ---------------------------------------------------------------------------
# initial_planner
# ---------------------------------------------------------------------------


def planner_has_synopsis(out: Any, ctx: dict[str, Any]) -> bool:
    return bool((_get(out, "synopsis") or "").strip())


def planner_archetype_count_ok(out: Any, ctx: dict[str, Any]) -> bool:
    archs = _get(out, "ideal_archetypes") or []
    lo = int(ctx.get("min_characters", 2))
    hi = int(ctx.get("max_characters", 32))
    return lo <= len([a for a in archs if str(a).strip()]) <= hi


def planner_matches_canonical(out: Any, ctx: dict[str, Any]) -> bool:
    """If the input carries a canonical roster, the planner must reproduce it."""
    canon = [c.strip().casefold() for c in (ctx.get("canonical_names") or [])]
    if not canon:
        return True  # nothing to match against
    got = [str(a).strip().casefold() for a in (_get(out, "ideal_archetypes") or [])]
    return set(canon).issubset(set(got))


# ---------------------------------------------------------------------------
# profile_batch_writer / profile_writer
# ---------------------------------------------------------------------------


def profiles_cover_all_names(out: Any, ctx: dict[str, Any]) -> bool:
    """Every requested name has a non-empty profile, names preserved verbatim."""
    names = [n.strip().casefold() for n in (ctx.get("character_names") or [])]
    if not names:
        return True
    items = out if isinstance(out, list) else (_get(out, "profiles") or [])
    got = {str(_get(p, "name", "")).strip().casefold() for p in items}
    if not set(names).issubset(got):
        return False
    return all(
        bool((_get(p, "profile_text") or "").strip()) for p in items
    )


def short_desc_within_cap(out: Any, ctx: dict[str, Any]) -> bool:
    items = out if isinstance(out, list) else [out]
    return all(len(_get(p, "short_description") or "") <= 240 for p in items)


# ---------------------------------------------------------------------------
# question_generator / next_question_generator
# ---------------------------------------------------------------------------


def questions_count_ok(out: Any, ctx: dict[str, Any]) -> bool:
    qs = _get(out, "questions") or (out if isinstance(out, list) else [])
    want = int(ctx.get("count", 0))
    return len(qs) >= want if want else len(qs) > 0


def options_count_ok(out: Any, ctx: dict[str, Any]) -> bool:
    max_opts = int(ctx.get("max_options", 4))
    qs = _get(out, "questions") or (out if isinstance(out, list) else [out])
    for q in qs:
        opts = _get(q, "options") or []
        if not (2 <= len(opts) <= max_opts):
            return False
        if any(not str(_get(o, "text") or "").strip() for o in opts):
            return False
    return True


def questions_unique(out: Any, ctx: dict[str, Any]) -> bool:
    qs = _get(out, "questions") or (out if isinstance(out, list) else [])
    texts = [
        " ".join(str(_get(q, "question_text") or "").split()).casefold() for q in qs
    ]
    texts = [t for t in texts if t]
    return len(texts) == len(set(texts))


def options_do_not_leak_outcomes(out: Any, ctx: dict[str, Any]) -> bool:
    """An option text must not just *be* an outcome name (trivially gameable)."""
    names = {n.strip().casefold() for n in (ctx.get("character_names") or [])}
    if not names:
        return True
    qs = _get(out, "questions") or (out if isinstance(out, list) else [out])
    for q in qs:
        for o in _get(q, "options") or []:
            if str(_get(o, "text") or "").strip().casefold() in names:
                return False
    return True


# ---------------------------------------------------------------------------
# INSTRUMENT RIGOR (2026-07-02): dimension tagging + coverage for validated
# instruments (MBTI/DISC/Big Five/…). These checks are inert (pass) when the
# record carries no ``instrument_dimensions`` — i.e. every non-instrument cell.
# Kept import-light: a local loose-key normalizer mirrors the backend's
# ``instrument_rigor.normalize_code`` semantics without importing the app.
# ---------------------------------------------------------------------------

_LOOSE_KEY_RE = re.compile(r"[^a-z0-9]+")


def _loose(value: Any) -> str:
    return _LOOSE_KEY_RE.sub("", str(value or "").casefold())


def _instrument_codes(ctx: dict[str, Any]) -> list[str]:
    """Canonical dimension codes for the cell's instrument, [] when N/A."""
    dims = ctx.get("instrument_dimensions") or []
    return [str(d).strip() for d in dims if str(d).strip()]


def _normalize_dim(value: Any, codes: list[str]) -> str | None:
    key = _loose(value)
    if not key:
        return None
    for c in codes:
        if key == _loose(c):
            return c
    return None


def _questions_of(out: Any) -> list[Any]:
    return _get(out, "questions") or (out if isinstance(out, list) else [out])


def instrument_dimensions_valid(out: Any, ctx: dict[str, Any]) -> bool:
    """Every question carries a ``dimension`` that maps onto a canonical code."""
    codes = _instrument_codes(ctx)
    if not codes:
        return True  # not an instrument cell
    for q in _questions_of(out):
        if _normalize_dim(_get(q, "dimension"), codes) is None:
            return False
    return True


def instrument_coverage_balanced(out: Any, ctx: dict[str, Any]) -> bool:
    """A question batch spreads across dimensions as evenly as possible.

    With n questions over k dimensions: when n >= k every dimension must be
    probed at least once and none more than ceil(n/k) times; when n < k all
    probed dimensions must be distinct. Unmappable/missing tags fail (they
    make honest coverage accounting impossible).
    """
    codes = _instrument_codes(ctx)
    if not codes:
        return True
    qs = _questions_of(out)
    tags: list[str] = []
    for q in qs:
        norm = _normalize_dim(_get(q, "dimension"), codes)
        if norm is None:
            return False
        tags.append(norm)
    n, k = len(tags), len(codes)
    counts = {c: tags.count(c) for c in codes}
    if n >= k:
        ceil_share = -(-n // k)
        return all(v >= 1 for v in counts.values()) and max(counts.values()) <= ceil_share
    return len(set(tags)) == len(tags)


def nqg_targets_under_covered(out: Any, ctx: dict[str, Any]) -> bool:
    """The single adaptive question probes one of the LEAST-covered dimensions."""
    codes = _instrument_codes(ctx)
    if not codes:
        return True
    asked = [
        _normalize_dim(a, codes)
        for a in (ctx.get("asked_dimensions") or [])
    ]
    counts = {c: 0 for c in codes}
    for a in asked:
        if a is not None:
            counts[a] += 1
    low = min(counts.values())
    under = {c for c in codes if counts[c] == low}
    got = _normalize_dim(_get(out, "dimension"), codes)
    return got is not None and got in under


# ---------------------------------------------------------------------------
# decision_maker
# ---------------------------------------------------------------------------


def decision_valid_action(out: Any, ctx: dict[str, Any]) -> bool:
    return _get(out, "action") in {"ASK_ONE_MORE_QUESTION", "FINISH_NOW"}


def decision_winner_when_finishing(out: Any, ctx: dict[str, Any]) -> bool:
    """If FINISH_NOW, a non-empty winner name must be present (no silent fallback)."""
    if _get(out, "action") != "FINISH_NOW":
        return True
    return bool((_get(out, "winning_character_name") or "").strip())


def decision_confidence_in_range(out: Any, ctx: dict[str, Any]) -> bool:
    c = _get(out, "confidence")
    if c is None:
        return True
    try:
        c = float(c)
    except Exception:
        return False
    return 0.0 <= c <= 1.0


# ---------------------------------------------------------------------------
# final_profile_writer
# ---------------------------------------------------------------------------


def final_is_substantive(out: Any, ctx: dict[str, Any]) -> bool:
    """Mirror the production gate: >= MIN paragraphs AND >= MIN chars."""
    desc = (_get(out, "description") or "").strip()
    return (
        len(desc) >= MIN_FINAL_DESCRIPTION_CHARS
        and _count_paragraphs(desc) >= MIN_FINAL_PARAGRAPHS
    )


# ---------------------------------------------------------------------------
# Registry + dispatch
# ---------------------------------------------------------------------------

CheckFn = Callable[[Any, dict[str, Any]], bool]

CHECKS: dict[str, CheckFn] = {
    "planner_has_synopsis": planner_has_synopsis,
    "planner_archetype_count_ok": planner_archetype_count_ok,
    "planner_matches_canonical": planner_matches_canonical,
    "profiles_cover_all_names": profiles_cover_all_names,
    "short_desc_within_cap": short_desc_within_cap,
    "questions_count_ok": questions_count_ok,
    "options_count_ok": options_count_ok,
    "questions_unique": questions_unique,
    "options_do_not_leak_outcomes": options_do_not_leak_outcomes,
    "instrument_dimensions_valid": instrument_dimensions_valid,
    "instrument_coverage_balanced": instrument_coverage_balanced,
    "nqg_targets_under_covered": nqg_targets_under_covered,
    "decision_valid_action": decision_valid_action,
    "decision_winner_when_finishing": decision_winner_when_finishing,
    "decision_confidence_in_range": decision_confidence_in_range,
    "final_is_substantive": final_is_substantive,
}


def run_checks(
    names: tuple[str, ...] | list[str], out: Any, ctx: dict[str, Any]
) -> dict[str, bool]:
    """Run named checks against an output, returning {check_name: passed}."""
    results: dict[str, bool] = {}
    for name in names:
        fn = CHECKS.get(name)
        if fn is None:
            continue
        try:
            results[name] = bool(fn(out, ctx))
        except Exception:
            results[name] = False
    return results
