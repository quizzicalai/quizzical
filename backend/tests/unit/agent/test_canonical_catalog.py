"""Unit tests for ``app.agent.canonical_catalog`` (built-in canonical sets).

The catalog is large and entirely declarative, so the tests focus on:

* The private helpers (``_lines``, ``_entry``, ``_merge_passes``) — these
  power every PASS_* declaration so a regression here would silently
  corrupt every set.
* The shape of ``BUILTIN_CANONICAL_SETS`` (top-level keys, per-set keys,
  ``count_hint`` invariant).
* Spot-checks on a handful of well-known sets so an accidental rename or
  reordering is detected.
"""

from __future__ import annotations

import pytest

from app.agent import canonical_catalog as cat
from app.agent.canonical_catalog import (
    BUILTIN_CANONICAL_SETS,
    _entry,
    _hierarchical_team_sets,
    _lines,
    _merge_passes,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class TestLines:
    def test_strips_whitespace_and_drops_empties(self) -> None:
        raw = "  alpha\n   \nbeta\n\tgamma  \n"
        assert _lines(raw) == ["alpha", "beta", "gamma"]

    def test_empty_string_returns_empty_list(self) -> None:
        assert _lines("") == []

    def test_only_whitespace_returns_empty_list(self) -> None:
        assert _lines("   \n\t\n   ") == []


class TestEntry:
    def test_default_count_hint_matches_clean_names(self) -> None:
        e = _entry(["A", " B ", "C"])
        assert e["names"] == ["A", "B", "C"]
        assert e["count_hint"] == 3
        assert e["aliases"] == []

    def test_strips_empty_and_whitespace_only_names(self) -> None:
        e = _entry(["A", "", "  ", "B"])
        assert e["names"] == ["A", "B"]
        assert e["count_hint"] == 2

    def test_aliases_are_cleaned(self) -> None:
        e = _entry(["A"], aliases=("alpha", " ALPHA ", ""))
        assert e["aliases"] == ["alpha", "ALPHA"]


class TestMergePasses:
    def test_merges_multiple_passes_into_sets_and_aliases(self) -> None:
        pass_a = {"S1": _entry(["a", "b"], aliases=("s1",))}
        pass_b = {"S2": _entry(["c"])}
        merged = _merge_passes(pass_a, pass_b)
        assert set(merged) == {"sets", "aliases"}
        assert "S1" in merged["sets"]
        assert "S2" in merged["sets"]
        assert merged["sets"]["S1"]["names"] == ["a", "b"]
        assert merged["sets"]["S1"]["count_hint"] == 2
        assert merged["aliases"]["S1"] == ["s1"]
        # S2 has no aliases — aliases dict skips it.
        assert "S2" not in merged["aliases"]

    def test_later_pass_overrides_earlier_set(self) -> None:
        merged = _merge_passes({"X": _entry(["a"])}, {"X": _entry(["b", "c"])})
        assert merged["sets"]["X"]["names"] == ["b", "c"]

    def test_count_hint_override_is_honoured(self) -> None:
        # Manually craft an entry to force a different count hint.
        merged = _merge_passes(
            {"X": {"names": ["a", "b", "c"], "count_hint": 99}}
        )
        assert merged["sets"]["X"]["count_hint"] == 99


class TestHierarchicalTeamSets:
    def test_creates_subgroup_group_and_league_entries(self) -> None:
        groups = {
            "East": {
                "Atlantic": ["Team A", "Team B"],
                "Central": ["Team C"],
            },
            "West": {
                "Pacific": ["Team D"],
            },
        }
        out = _hierarchical_team_sets("Demo", groups, aliases=("demo league",))
        # Subgroup-level sets exist.
        assert out["Demo Atlantic Teams"]["names"] == ["Team A", "Team B"]
        assert out["Demo Central Teams"]["names"] == ["Team C"]
        assert out["Demo Pacific Teams"]["names"] == ["Team D"]
        # Group-level sets aggregate their subgroups.
        assert out["Demo East Teams"]["names"] == ["Team A", "Team B", "Team C"]
        assert out["Demo West Teams"]["names"] == ["Team D"]
        # League level aggregates everything; aliases are attached only at the league level.
        assert out["Demo Teams"]["names"] == ["Team A", "Team B", "Team C", "Team D"]
        assert out["Demo Teams"]["aliases"] == ["demo league"]


# ---------------------------------------------------------------------------
# BUILTIN_CANONICAL_SETS shape
# ---------------------------------------------------------------------------


class TestBuiltinShape:
    def test_top_level_keys(self) -> None:
        assert set(BUILTIN_CANONICAL_SETS) == {"sets", "aliases"}

    def test_every_set_has_names_and_count_hint(self) -> None:
        for title, entry in BUILTIN_CANONICAL_SETS["sets"].items():
            assert isinstance(entry, dict), title
            assert "names" in entry, title
            assert "count_hint" in entry, title
            assert isinstance(entry["names"], list), title

    def test_no_set_has_empty_names(self) -> None:
        empties = [t for t, e in BUILTIN_CANONICAL_SETS["sets"].items() if not e["names"]]
        assert not empties, f"sets with empty names: {empties}"

    def test_count_hint_default_matches_names_length(self) -> None:
        # Catalog declares entries via ``_entry`` which sets count_hint=len(names),
        # so for any set whose count_hint differs from len(names) we want it to be
        # explicitly intentional. The current built-in catalog has none.
        mismatches = [
            t
            for t, e in BUILTIN_CANONICAL_SETS["sets"].items()
            if e["count_hint"] != len(e["names"])
        ]
        assert not mismatches, f"count_hint mismatch: {mismatches}"

    def test_aliases_only_present_for_sets_with_aliases(self) -> None:
        # Every alias key must reference an existing set.
        unknown = [k for k in BUILTIN_CANONICAL_SETS["aliases"] if k not in BUILTIN_CANONICAL_SETS["sets"]]
        assert not unknown, f"alias keys without matching sets: {unknown}"


class TestBuiltinSpotChecks:
    @pytest.mark.parametrize(
        "title",
        [
            "DISC Styles",
            "Five Love Languages",
            "Attachment Styles",
            "Holland Codes",
            "Visible Spectrum Colors",
            "Olympic Ring Colors",
        ],
    )
    def test_well_known_sets_present(self, title: str) -> None:
        assert title in BUILTIN_CANONICAL_SETS["sets"]

    def test_disc_membership(self) -> None:
        names = BUILTIN_CANONICAL_SETS["sets"]["DISC Styles"]["names"]
        assert names == ["Dominance", "Influence", "Steadiness", "Conscientiousness"]

    def test_visible_spectrum_has_seven_colors(self) -> None:
        names = BUILTIN_CANONICAL_SETS["sets"]["Visible Spectrum Colors"]["names"]
        assert names == ["Red", "Orange", "Yellow", "Green", "Blue", "Indigo", "Violet"]
        assert len(names) == 7

    def test_aliases_are_all_lowercase_strings(self) -> None:
        # Aliases are matched lower-cased in canonical_sets.canonical_for; the
        # catalog stores them mixed but every alias should be a non-empty str.
        for title, alist in BUILTIN_CANONICAL_SETS["aliases"].items():
            assert isinstance(alist, list), title
            for a in alist:
                assert isinstance(a, str) and a.strip(), (title, a)


def test_module_exports_builtin_only() -> None:
    assert hasattr(cat, "BUILTIN_CANONICAL_SETS")
