"""Offline RESOLUTION evaluator (deterministic routing — NO LLM / FAL keys).

This is the owner's proof-of-quality gate for the canonical / dimension / depth
correctness fixes (PRs #43 canonical-correctness, #44 planning-depth,
#45 canonical-growth). For each LABELED acceptance topic it runs the REAL,
PURE-PYTHON resolution path:

  * ``analyze_topic(topic)``        — domain/outcome-kind routing + normalized title
  * ``canonical_for(topic)``        — exact canonical member list (if any)
  * ``canonical_outcome_mode(topic)``  — "single" | "blended" (or None)
  * ``canonical_title_for(topic)``  — resolved canonical SET TITLE (if any)
  * ``graph._effective_depth_bounds(topic)`` — topic-aware (eff_min, eff_max)

and checks the resolution against the owner's acceptance bar (categories A–F in
``specifications`` / the scratchpad acceptance set). NO network, NO LLM, NO FAL —
every signal here is computed from the in-process canonical catalog + the
deterministic intent classifier, so this script is safe to run with no keys.

The acceptance categories:

  A. Canonical SINGLE   — exact member set + outcome_mode "single".
  B. Canonical BLENDED  — palette + outcome_mode "blended"; plus the ocean casing
                          rule ("ocean"/"Ocean" -> geographic Oceans single,
                          "OCEAN" -> Big Five blended).
  C. DIMENSION          — explicit fandom qualifier -> outcome_kind "dimension"
                          with the right normalized TITLE/casing (+ members where a
                          canonical set exists).
  D. AMBIGUOUS fandom   — bare fandom defaults to CHARACTERS (NOT a dimension).
  E. MUST-NOT-MISFIRE   — non-fandom <noun+dimension> never routes to "dimension".
  F. CASUAL             — non-canonical topic -> characters/types/archetypes, never
                          a misfired dimension, and the depth floor collapses to 12.

The pass bar is A–E at 100%. The script prints a PER-CATEGORY precision table and
lists EVERY miss as ``topic | expected | got`` so failures are immediately
actionable. Exit code is non-zero if any A–E case misses (CI / nightly friendly).

USAGE
-----
    cd backend
    APP_ENVIRONMENT=local LOG_TO_FILE=false python -m scripts.eval_resolution
    # or:  python scripts/eval_resolution.py [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# Offline + deterministic: force the local config and never write log files.
os.environ.setdefault("APP_ENVIRONMENT", "local")
os.environ.setdefault("LOG_TO_FILE", "false")

# Make ``scripts`` runnable as ``python -m scripts.eval_resolution`` AND as a
# direct file path: ensure the backend root is importable.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.agent.canonical_sets import (  # noqa: E402
    canonical_for,
    canonical_outcome_mode,
    canonical_title_for,
    min_items_for,
)
from app.agent.graph import _effective_depth_bounds  # noqa: E402
from app.agent.tools.intent_classification import analyze_topic  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Owner ceiling + global floor (must match graph._effective_depth_bounds).
HARD_MAX = 24
GLOBAL_FLOOR = 12


def _foldset(names: list[str] | None) -> set[str]:
    return {n.strip().casefold() for n in (names or []) if n and n.strip()}


def _members_match(got: list[str] | None, expected: list[str]) -> bool:
    """Order-independent, case/whitespace-folded exact set equality."""
    return _foldset(got) == _foldset(expected)


def _outcome_kind(topic: str) -> str:
    return str(analyze_topic(topic).get("outcome_kind") or "")


def _normalized(topic: str) -> str:
    return str(analyze_topic(topic).get("normalized_category") or "")


# ---------------------------------------------------------------------------
# Acceptance set (members reconciled against the LIVE catalog on 2026-06-30)
# ---------------------------------------------------------------------------


@dataclass
class Case:
    """One acceptance topic + the predicate that decides PASS/FAIL.

    ``check`` returns (ok, expected_str, got_str). ``expected``/``got`` describe
    the resolution so a miss is reported as ``topic | expected | got``.
    """

    topic: str
    expected: str
    check: Callable[[str], tuple[bool, str]]


def single_case(topic: str, members: list[str]) -> Case:
    """Category A: exact canonical set + outcome_mode 'single'."""

    def check(t: str) -> tuple[bool, str]:
        got_members = canonical_for(t)
        mode = canonical_outcome_mode(t)
        ok = _members_match(got_members, members) and mode == "single"
        title = canonical_title_for(t)
        got = f"mode={mode!r} title={title!r} members={got_members}"
        return ok, got

    return Case(topic, f"single + {len(members)} members {members}", check)


def blended_case(topic: str, palette: list[str]) -> Case:
    """Category B: exact palette + outcome_mode 'blended'."""

    def check(t: str) -> tuple[bool, str]:
        got_members = canonical_for(t)
        mode = canonical_outcome_mode(t)
        ok = _members_match(got_members, palette) and mode == "blended"
        title = canonical_title_for(t)
        got = f"mode={mode!r} title={title!r} palette={got_members}"
        return ok, got

    return Case(topic, f"blended + palette {palette}", check)


def title_member_single_case(topic: str, title: str, members: list[str]) -> Case:
    """Category B (ocean geographic): exact set + single + resolved TITLE."""

    def check(t: str) -> tuple[bool, str]:
        got_members = canonical_for(t)
        mode = canonical_outcome_mode(t)
        got_title = canonical_title_for(t)
        ok = (
            _members_match(got_members, members)
            and mode == "single"
            and got_title == title
        )
        return ok, f"mode={mode!r} title={got_title!r} members={got_members}"

    return Case(topic, f"single '{title}' + members {members}", check)


def dimension_case(
    topic: str, title: str, members: list[str] | None = None
) -> Case:
    """Category C: outcome_kind 'dimension' + right normalized TITLE/casing.

    When ``members`` is given (a canonical set backs the dimension, e.g. LOTR
    Races / Hogwarts Houses) those members must match exactly too.
    """

    def check(t: str) -> tuple[bool, str]:
        kind = _outcome_kind(t)
        norm = _normalized(t)
        ok = kind == "dimension" and norm == title
        got_members = canonical_for(t)
        if members is not None:
            ok = ok and _members_match(got_members, members)
        got = f"kind={kind!r} normalized={norm!r}"
        if members is not None:
            got += f" members={got_members}"
        return ok, got

    exp = f"dimension '{title}'"
    if members is not None:
        exp += f" + members {members}"
    return Case(topic, exp, check)


def characters_case(topic: str, normalized_suffix: str = "Characters") -> Case:
    """Category D: bare fandom -> CHARACTERS (NOT a dimension)."""

    def check(t: str) -> tuple[bool, str]:
        kind = _outcome_kind(t)
        norm = _normalized(t)
        ok = kind == "characters" and norm.endswith(normalized_suffix)
        return ok, f"kind={kind!r} normalized={norm!r}"

    return Case(topic, f"characters (normalized ends '{normalized_suffix}')", check)


def not_dimension_case(topic: str) -> Case:
    """Category E: must NOT misfire as a dimension."""

    def check(t: str) -> tuple[bool, str]:
        kind = _outcome_kind(t)
        ok = kind != "dimension"
        norm = _normalized(t)
        return ok, f"kind={kind!r} normalized={norm!r}"

    return Case(topic, "NOT dimension", check)


def casual_case(topic: str) -> Case:
    """Category F: casual/non-canonical -> characters/types/archetypes, depth 12."""

    def check(t: str) -> tuple[bool, str]:
        kind = _outcome_kind(t)
        eff_min, eff_max = _effective_depth_bounds(t)
        # casual & non-canonical: not a misfired dimension, floor collapses to 12.
        ok = (
            kind in {"characters", "types", "archetypes"}
            and kind != "dimension"
            and eff_min == GLOBAL_FLOOR
            and eff_max == HARD_MAX
        )
        return ok, f"kind={kind!r} eff=({eff_min},{eff_max})"

    return Case(topic, "characters/types, depth floor 12", check)


# --- per-instrument depth floors (from the canonical catalog min_items) -------
# DISC ~22, MBTI ~24, Big Five ~20, Enneagram / RIASEC ~18; casual ~12.
DEPTH_FLOORS: dict[str, int] = {
    "DISC": 22,
    "MBTI": 24,
    "Big Five": 20,
    "OCEAN": 20,
    "Enneagram": 18,
    "RIASEC": 18,
    "Holland Codes": 18,
}


def depth_case(topic: str, expected_floor: int) -> Case:
    """Depth bar: eff_min hits the per-instrument floor, eff_max == HARD_MAX,
    and eff_min stays within [12, 24]."""

    def check(t: str) -> tuple[bool, str]:
        eff_min, eff_max = _effective_depth_bounds(t)
        ok = (
            eff_min == expected_floor
            and eff_max == HARD_MAX
            and GLOBAL_FLOOR <= eff_min <= HARD_MAX
        )
        mi = min_items_for(t)
        return ok, f"eff=({eff_min},{eff_max}) min_items={mi}"

    return Case(topic, f"eff_min={expected_floor}, eff_max={HARD_MAX}", check)


# ---------------------------------------------------------------------------
# Acceptance categories
# ---------------------------------------------------------------------------

MBTI_16 = [
    "ISTJ", "ISFJ", "INFJ", "INTJ", "ISTP", "ISFP", "INFP", "INTP",
    "ESTP", "ESFP", "ENFP", "ENTP", "ESTJ", "ESFJ", "ENFJ", "ENTJ",
]
ENNEAGRAM_9 = [
    "Type 1 The Reformer", "Type 2 The Helper", "Type 3 The Achiever",
    "Type 4 The Individualist", "Type 5 The Investigator", "Type 6 The Loyalist",
    "Type 7 The Enthusiast", "Type 8 The Challenger", "Type 9 The Peacemaker",
]
HOGWARTS_4 = ["Gryffindor", "Slytherin", "Ravenclaw", "Hufflepuff"]
DND_9 = [
    "Lawful Good", "Neutral Good", "Chaotic Good",
    "Lawful Neutral", "True Neutral", "Chaotic Neutral",
    "Lawful Evil", "Neutral Evil", "Chaotic Evil",
]
LOVE_5 = [
    "Words of Affirmation", "Acts of Service", "Receiving Gifts",
    "Quality Time", "Physical Touch",
]
ZODIAC_12 = [
    "Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
    "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces",
]
TEMPERAMENTS_4 = ["Sanguine", "Choleric", "Melancholic", "Phlegmatic"]
ATTACHMENT_4 = [
    "Secure", "Anxious-Preoccupied", "Dismissive-Avoidant", "Fearful-Avoidant",
]
RIASEC_6 = [
    "Realistic", "Investigative", "Artistic", "Social", "Enterprising",
    "Conventional",
]
DISC_PALETTE = ["Dominance", "Influence", "Steadiness", "Conscientiousness"]
BIG5_PALETTE = [
    "Openness", "Conscientiousness", "Extraversion", "Agreeableness",
    "Neuroticism",
]
OCEANS_5 = ["Arctic", "Atlantic", "Indian", "Pacific", "Southern"]
LOTR_RACES = ["Hobbits", "Elves", "Dwarves", "Men", "Orcs", "Ents"]


def build_categories() -> dict[str, list[Case]]:
    return {
        "A. canonical-single": [
            single_case("MBTI", MBTI_16),
            single_case("Myers-Briggs", MBTI_16),
            single_case("16 personalities", MBTI_16),
            single_case("What is my MBTI type", MBTI_16),
            single_case("Enneagram", ENNEAGRAM_9),
            single_case("Hogwarts Houses", HOGWARTS_4),
            single_case("which hogwarts house am i", HOGWARTS_4),
            single_case("D&D Alignment", DND_9),
            single_case("Love Languages", LOVE_5),
            single_case("Zodiac signs", ZODIAC_12),
            single_case("Four Temperaments", TEMPERAMENTS_4),
            single_case("Attachment styles", ATTACHMENT_4),
            single_case("RIASEC", RIASEC_6),
            single_case("Holland Codes", RIASEC_6),
        ],
        "B. canonical-blended + palette": [
            blended_case("DISC", DISC_PALETTE),
            blended_case("DISC profile", DISC_PALETTE),
            blended_case("DISC assessment", DISC_PALETTE),
            blended_case("What is my DISC type", DISC_PALETTE),
            blended_case("DISC personality", DISC_PALETTE),
            blended_case("Big Five", BIG5_PALETTE),
            blended_case("OCEAN", BIG5_PALETTE),
            blended_case("big 5", BIG5_PALETTE),
            # casing rule: lowercase/Title 'ocean' -> geographic Oceans (single)
            title_member_single_case("ocean", "Oceans", OCEANS_5),
            title_member_single_case("Ocean", "Oceans", OCEANS_5),
        ],
        "C. dimension": [
            dimension_case("Lord of the Rings Race", "Lord of the Rings Race", LOTR_RACES),
            dimension_case("LOTR races", "LOTR races", LOTR_RACES),
            dimension_case("Harry Potter House", "Harry Potter House", HOGWARTS_4),
            dimension_case("Star Wars faction", "Star Wars Factions"),
            dimension_case("Star Wars factions", "Star Wars Factions"),
            dimension_case("Avatar elements", "Avatar Elements"),
            dimension_case("Hogwarts class", "Hogwarts Classes"),
        ],
        "D. ambiguous-fandom -> characters": [
            characters_case("Lord of the Rings"),
            characters_case("Harry Potter"),
            characters_case("Star Wars"),
            characters_case("Game of Thrones"),
        ],
        "E. must-not-misfire (NOT dimension)": [
            not_dimension_case("master class"),
            not_dimension_case("chemical element"),
            not_dimension_case("Religious Order"),
            not_dimension_case("rat race"),
            not_dimension_case("space race"),
            not_dimension_case("human race"),
            not_dimension_case("social class"),
            not_dimension_case("middle class"),
            not_dimension_case("first class"),
            not_dimension_case("personality type"),
        ],
        "F. casual -> characters/types, depth 12": [
            casual_case("Types of coffee drinkers"),
            casual_case("Which sandwich am I"),
            casual_case("obscure niche topic xyz"),
        ],
        "DEPTH. per-instrument floors": [
            depth_case("DISC", DEPTH_FLOORS["DISC"]),
            depth_case("MBTI", DEPTH_FLOORS["MBTI"]),
            depth_case("Big Five", DEPTH_FLOORS["Big Five"]),
            depth_case("OCEAN", DEPTH_FLOORS["OCEAN"]),
            depth_case("Enneagram", DEPTH_FLOORS["Enneagram"]),
            depth_case("RIASEC", DEPTH_FLOORS["RIASEC"]),
            depth_case("Holland Codes", DEPTH_FLOORS["Holland Codes"]),
            depth_case("Types of coffee drinkers", GLOBAL_FLOOR),
        ],
    }


# Categories that count toward the 100% pass bar (A–E). DEPTH + F are reported
# and gate too (depth floor 12 is part of F; per-instrument depth is its own
# section), but the owner's explicit "100%" bar is A–E.
HARD_BAR = ("A", "B", "C", "D", "E")


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


@dataclass
class CaseResult:
    category: str
    topic: str
    expected: str
    got: str
    ok: bool


@dataclass
class Report:
    results: list[CaseResult] = field(default_factory=list)

    def by_category(self) -> dict[str, list[CaseResult]]:
        out: dict[str, list[CaseResult]] = {}
        for r in self.results:
            out.setdefault(r.category, []).append(r)
        return out

    def misses(self) -> list[CaseResult]:
        return [r for r in self.results if not r.ok]


def evaluate(categories: dict[str, list[Case]]) -> Report:
    report = Report()
    for cat, cases in categories.items():
        for case in cases:
            try:
                ok, got = case.check(case.topic)
            except Exception as exc:  # noqa: BLE001 — never a silent pass
                ok, got = False, f"ERROR {type(exc).__name__}: {exc}"
            report.results.append(
                CaseResult(cat, case.topic, case.expected, got, ok)
            )
    return report


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def render(report: Report) -> str:
    by_cat = report.by_category()
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("RESOLUTION EVAL  (offline, deterministic — no LLM/FAL keys)")
    lines.append("=" * 78)
    lines.append(f"{'CATEGORY':<44}{'PASS':>6}{'TOTAL':>7}{'PRECISION':>11}")
    lines.append("-" * 78)
    total_pass = total = 0
    hard_bar_clean = True
    for cat, results in by_cat.items():
        n_pass = sum(1 for r in results if r.ok)
        n = len(results)
        total_pass += n_pass
        total += n
        prec = (n_pass / n * 100) if n else 100.0
        lines.append(f"{cat:<44}{n_pass:>6}{n:>7}{prec:>10.1f}%")
        if cat[0] in HARD_BAR and n_pass != n:
            hard_bar_clean = False
    lines.append("-" * 78)
    overall = (total_pass / total * 100) if total else 100.0
    lines.append(f"{'OVERALL':<44}{total_pass:>6}{total:>7}{overall:>10.1f}%")
    lines.append("=" * 78)

    misses = report.misses()
    if misses:
        lines.append("")
        lines.append(f"MISSES ({len(misses)})  [topic | expected | got]")
        lines.append("-" * 78)
        for r in misses:
            lines.append(f"  [{r.category[:1]}] {r.topic} | {r.expected} | {r.got}")
    else:
        lines.append("")
        lines.append("NO MISSES — every acceptance case resolved as expected.")

    lines.append("")
    bar = "CLEAN (100%)" if hard_bar_clean else "FAILED — see misses above"
    lines.append(f"A-E HARD BAR: {bar}")
    return "\n".join(lines)


def render_json(report: Report) -> str:
    by_cat = report.by_category()
    payload: dict[str, Any] = {"categories": {}, "misses": []}
    for cat, results in by_cat.items():
        n_pass = sum(1 for r in results if r.ok)
        payload["categories"][cat] = {
            "pass": n_pass,
            "total": len(results),
            "precision": round(n_pass / len(results) * 100, 1) if results else 100.0,
        }
    for r in report.misses():
        payload["misses"].append(
            {"category": r.category, "topic": r.topic, "expected": r.expected, "got": r.got}
        )
    return json.dumps(payload, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="eval_resolution",
        description="Offline deterministic resolution eval (A-E hard bar).",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of a table.")
    args = parser.parse_args()

    report = evaluate(build_categories())
    print(render_json(report) if args.json else render(report))

    # Non-zero exit if any A-E case missed (the owner's explicit 100% bar).
    failed = any(r for r in report.misses() if r.category[0] in HARD_BAR)
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
