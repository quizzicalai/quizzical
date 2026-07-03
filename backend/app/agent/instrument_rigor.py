# backend/app/agent/instrument_rigor.py
"""INSTRUMENT RIGOR — assessment-grade questioning for validated instruments.

Owner blackbox finding #5 (2026-07-02): "Serious topics like Myers-Briggs or
DISC need more scientific rigour in how it asks questions." This module is the
single source for the conditional INSTRUMENT RIGOR prompt block and its
dimension-coverage bookkeeping:

* :func:`instrument_spec_for` resolves a topic to an :class:`InstrumentSpec`
  IFF the topic maps to a canonical set that carries ``dimensions`` (only the
  tagged rigorous instruments: MBTI, DISC, Big Five, Enneagram, Holland Codes).
  Every other topic — including canonical-but-casual sets like Hogwarts Houses
  and free-text whimsy like "what type of troll am i" — resolves to ``None``,
  so the whimsical questioning path is untouched byte-for-byte.
* :meth:`InstrumentSpec.render_question_block` renders the block injected into
  the ``question_generator`` / ``next_question_generator`` prompts via the
  ``{instrument_rigor}`` template variable ("" when not an instrument). With
  ``asked_dimensions`` it additionally reports per-dimension coverage and
  directs the adaptive generator at the LEAST-COVERED dimension so a finished
  quiz covers all dimensions.
* :meth:`InstrumentSpec.render_plan_block` is the shorter planner variant.
* :meth:`InstrumentSpec.normalize_code` snaps a model-emitted ``dimension``
  value onto the canonical code (e.g. "e/i", "E-I", or "Extraversion vs
  Introversion" -> "E/I") so coverage counting is reliable.

The dimension DATA lives in ``canonical_catalog`` (data-only) and is served
through ``canonical_sets.dimensions_for`` (App-Config overlay-safe).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.agent.canonical_sets import canonical_title_for, dimensions_for

__all__ = [
    "InstrumentSpec",
    "instrument_spec_for",
    "render_instrument_rigor_block",
]

_CODE_CLEAN_RE = re.compile(r"[^a-z0-9]+")


def _loose_key(value: Any) -> str:
    """Casefold and strip all non-alphanumerics: 'E/I' == 'e-i' == 'E I'."""
    return _CODE_CLEAN_RE.sub("", str(value or "").casefold())


@dataclass(frozen=True)
class InstrumentSpec:
    """A resolved validated instrument: canonical title + measured dimensions."""

    title: str
    category: str  # the caller-supplied string that resolved
    dimensions: tuple[dict[str, Any], ...]

    @property
    def codes(self) -> list[str]:
        return [d["code"] for d in self.dimensions]

    # -- normalization ------------------------------------------------------

    def normalize_code(self, value: Any) -> str | None:
        """Snap a loose model-emitted dimension label onto a canonical code.

        Accepts the code itself in any casing/punctuation ("e/i", "E-I"), the
        dimension name ("Extraversion vs Introversion"), or a "code — name"
        composite. Returns ``None`` when nothing matches (the question is then
        treated as not dimension-tagged rather than mis-counted).
        """
        key = _loose_key(value)
        if not key:
            return None
        for d in self.dimensions:
            code_key = _loose_key(d["code"])
            name_key = _loose_key(d.get("name"))
            if key == code_key or (name_key and key == name_key):
                return d["code"]
            # Composite forms like "E/I (Extraversion vs Introversion)".
            if code_key and key.startswith(code_key) and name_key and name_key in key:
                return d["code"]
        return None

    # -- coverage -----------------------------------------------------------

    def coverage(self, asked_dimensions: list[str] | None) -> dict[str, int]:
        """Per-code count of how often each dimension has been probed so far."""
        counts: dict[str, int] = dict.fromkeys(self.codes, 0)
        for raw in asked_dimensions or []:
            code = self.normalize_code(raw)
            if code is not None:
                counts[code] += 1
        return counts

    def under_covered(self, asked_dimensions: list[str] | None) -> list[str]:
        """Codes tied for the LOWEST probe count, in canonical order."""
        counts = self.coverage(asked_dimensions)
        if not counts:
            return []
        low = min(counts.values())
        return [c for c in self.codes if counts[c] == low]

    # -- prompt blocks --------------------------------------------------------

    def _dimension_lines(self) -> str:
        lines: list[str] = []
        for d in self.dimensions:
            poles = "; ".join(d.get("poles") or [])
            label = d.get("name") or d["code"]
            if poles:
                lines.append(f"  • \"{d['code']}\" ({label}): {poles}.")
            else:
                lines.append(f"  • \"{d['code']}\" ({label}).")
        return "\n".join(lines)

    def render_question_block(
        self, *, asked_dimensions: list[str] | None = None
    ) -> str:
        """The INSTRUMENT RIGOR block for question generation.

        Pass ``asked_dimensions`` (the ``dimension`` tags of every question
        generated so far) on the ADAPTIVE path to get the coverage report and
        the explicit least-covered target. Baseline generation omits it.
        """
        parts: list[str] = [
            f"## INSTRUMENT RIGOR — {self.title}\n"
            "This topic is a validated assessment framework, NOT entertainment. "
            "Questioning must follow assessment discipline; tone stays approachable "
            "but measured. These rules take precedence over any playful/whimsical "
            "guidance for this topic:\n"
            "- The instrument measures EXACTLY these dimensions (probe ONLY these):\n"
            f"{self._dimension_lines()}\n"
            "- ONE dimension per question: each question probes exactly one dimension; "
            "set the question's \"dimension\" field to that dimension's code "
            f"(e.g. \"{self.codes[0]}\").\n"
            "- BALANCED coverage: distribute questions as evenly as possible across "
            "ALL dimensions listed above; never cluster on one dimension.\n"
            "- Behavioural/situational framing: ask how the person ACTUALLY behaves "
            "in a concrete situation (\"When plans change at the last minute, you "
            "usually…\"), never self-labels, trait ratings, or agree/disagree "
            "statements (\"Are you organised?\").\n"
            "- Neutral, non-leading wording: no pole may sound more flattering, "
            "healthy, or socially desirable than another; a reader must not be able "
            "to tell which answer is \"better\".\n"
            "- Answer options map cleanly onto DISTINCT poles/levels of the probed "
            "dimension: include at least one option that clearly signals each pole, "
            "and never mix in options that measure a different dimension.\n"
            "- No astrology-style flattery and no vague statements that could apply "
            "to anyone."
        ]
        if asked_dimensions is not None:
            counts = self.coverage(asked_dimensions)
            low = self.under_covered(asked_dimensions)
            cov = ", ".join(f"{c}: {counts[c]}" for c in self.codes)
            parts.append(f"- Coverage so far (questions per dimension): {cov}.")
            if low and len(low) < len(self.codes):
                target = low[0]
                parts.append(
                    f"- UNDER-COVERED dimensions: {', '.join(low)}. Your next "
                    f"question MUST probe \"{target}\" (the least-covered "
                    "dimension) — pick whichever question strategy best serves it."
                )
            elif low:
                parts.append(
                    "- All dimensions are equally covered so far. Probe the dimension "
                    "where the user's previous answers leave the MOST ambiguity."
                )
        return "\n".join(parts) + "\n\n"

    def render_plan_block(self) -> str:
        """The shorter INSTRUMENT RIGOR block for the initial planner."""
        dims = "; ".join(
            f"{d['code']} ({d.get('name') or d['code']})" for d in self.dimensions
        )
        return (
            f"## INSTRUMENT RIGOR — {self.title}\n"
            "This topic is a validated assessment framework — plan an "
            "assessment-grade quiz, not entertainment. Keep the title and synopsis "
            "measured and factual (no astrology-style flattery), use the canonical "
            "outcomes verbatim, and note that questions will probe the instrument's "
            f"measured dimensions with balanced coverage: {dims}.\n\n"
        )


def instrument_spec_for(*categories: str | None) -> InstrumentSpec | None:
    """Resolve the first candidate string that maps to an instrument with dimensions.

    Callers typically pass ``(analysis["normalized_category"], raw_category)`` —
    the same double-resolve pattern as ``canonical_hint_block``. Returns ``None``
    for every non-instrument topic (the OFF path: whimsy untouched).
    """
    for cat in categories:
        if not cat or not str(cat).strip():
            continue
        dims = dimensions_for(cat)
        if not dims:
            continue
        title = canonical_title_for(cat) or str(cat)
        return InstrumentSpec(
            title=title, category=str(cat), dimensions=tuple(dims)
        )
    return None


def render_instrument_rigor_block(
    *categories: str | None, asked_dimensions: list[str] | None = None
) -> str:
    """Convenience: the question-path block, or "" for non-instrument topics."""
    spec = instrument_spec_for(*categories)
    if spec is None:
        return ""
    return spec.render_question_block(asked_dimensions=asked_dimensions)
