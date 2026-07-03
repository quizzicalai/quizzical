"""Dataset loading + prompt-context assembly for each function.

A dataset fixture (``evals/datasets/<function>.json``) is a small, version-
controlled list of input records. Each record carries:

  * ``input_id``          -- stable id (used to pair observations across variants)
  * ``category``/``bucket``-- topic + bucket (canonical|media|open|serious)
  * ``canonical_names``   -- ground-truth roster when known (enables ref-guided
                             judging + the ``*_matches_canonical`` checks)
  * function-specific context (e.g. ``character_names`` for profile writers,
    ``quiz_history`` for adaptive/decision/final steps)

``assemble_context`` turns a record into the ``str.format`` kwargs the production
prompt template expects, filling sane defaults so a single fixture can be reused
across related functions. This keeps fixtures tiny and the rationale explicit:
small N x diverse buckets is enough to estimate per-function quality because the
unit of replication is the (input x rep) cell, not the dataset size (see
methodology, "Representative datasets").
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_EVALS_ROOT = Path(__file__).resolve().parents[1]


def load_dataset(rel_path: str) -> list[dict[str, Any]]:
    """Load a dataset fixture given a path relative to ``evals/``."""
    p = _EVALS_ROOT / rel_path
    data = json.loads(p.read_text(encoding="utf-8"))
    records = data["records"] if isinstance(data, dict) else data
    if not isinstance(records, list):
        raise ValueError(f"dataset {rel_path} must be a list (or {{'records': [...]}})")
    return records


def _kind_mode_intent(bucket: str) -> tuple[str, str, str]:
    """Deterministic substitute for the (no-LLM) topic_normalizer, matching
    Analysis/harness.py so artifacts look production-shaped."""
    return {
        "canonical": ("types", "factual", "identify"),
        "media": ("characters", "balanced", "identify"),
        "serious": ("profiles", "factual", "career"),
        # Validated instruments (MBTI/DISC/Big Five/…) — INSTRUMENT RIGOR cells.
        "instrument": ("types", "factual", "identify"),
    }.get(bucket, ("types", "whimsical", "identify"))


def _character_contexts_for(category: str, names: list[str]) -> str:
    """Mirror production's PBW grounding input (AC-EVAL-2026-07-02).

    ``draft_character_profiles`` now feeds a 1-line canonical hint per name via
    ``canonical_hint_block`` when the topic resolves to a canonical set, and ""
    otherwise. The eval must exercise the same input or it scores a different
    task than prod runs. Falls back to "" (the non-canonical production value)
    when the backend isn't importable (dry-run isolation).
    """
    try:
        from .prompts_adapter import _ensure_backend_on_path

        if _ensure_backend_on_path():
            from app.agent.tools.content_creation_tools import canonical_hint_block

            return canonical_hint_block(category, list(names or []))
    except Exception:
        pass
    return ""


def _instrument_rigor_for(
    function: str, category: str, record: dict[str, Any]
) -> str:
    """Mirror production's INSTRUMENT RIGOR input (owner blackbox #5).

    ``generate_baseline_questions`` / ``generate_next_question`` /
    ``plan_quiz`` fill the ``{instrument_rigor}`` template variable with the
    rendered block for validated-instrument topics and "" otherwise. The eval
    must exercise the same input or it scores a different task than prod runs.
    Falls back to "" (the non-instrument production value) when the backend
    isn't importable (dry-run isolation).
    """
    try:
        from .prompts_adapter import _ensure_backend_on_path

        if _ensure_backend_on_path():
            from app.agent.instrument_rigor import instrument_spec_for

            spec = instrument_spec_for(category)
            if spec is None:
                return ""
            if function == "next_question_generator":
                return spec.render_question_block(
                    asked_dimensions=list(record.get("asked_dimensions") or [])
                )
            if function == "initial_planner":
                return spec.render_plan_block()
            return spec.render_question_block()
    except Exception:
        pass
    return ""


def assemble_context(function: str, record: dict[str, Any]) -> dict[str, Any]:
    """Build the format kwargs for a function's prompt from a dataset record.

    Returns a dict that is a superset of any single prompt's needs; ``str.format``
    on a template only consumes the keys it references.
    """
    category = record.get("category", "")
    bucket = record.get("bucket", "open")
    okind, cmode, intent = _kind_mode_intent(bucket)
    canonical = record.get("canonical_names") or []
    roster = [str(n) for n in (record.get("character_names") or canonical or [])]
    if function == "profile_batch_writer":
        # Mirror draft_character_profiles exactly: names as an enumerated
        # "1. Name" block and count = len(roster). The pre-2026-07-02 harness
        # rendered the raw list repr and defaulted count to 6 even with a
        # 4-5 name roster, so the prompt demanded MORE profiles than names --
        # an input-fidelity bug that penalised every variant's coverage.
        character_names_value: Any = "\n".join(
            f"{i}. {name}" for i, name in enumerate(roster, start=1)
        )
        count_default = len(roster) or 6
    else:
        character_names_value = record.get("character_names", canonical)
        count_default = 6
    ctx: dict[str, Any] = {
        "category": category,
        "normalized_category": category,
        "bucket": bucket,
        "outcome_kind": record.get("outcome_kind", okind),
        "creativity_mode": record.get("creativity_mode", cmode),
        "intent": record.get("intent", intent),
        "canonical_names": ", ".join(canonical) if canonical else "(none)",
        "search_context": record.get("search_context", "(none)"),
        "synopsis": record.get("synopsis", ""),
        "max_options": record.get("max_options", 4),
        "count": record.get("count", count_default),
        "min_characters": record.get("min_characters", 4),
        "max_characters": record.get("max_characters", 32),
        # for profile writers
        "character_names": character_names_value,
        "character_name": (roster or ["Outcome"])[0],
        # Production-shaped grounding: canonical hints when the topic resolves,
        # "" otherwise (exactly what draft_character_profiles now passes).
        "character_contexts": _character_contexts_for(category, roster),
        "character_context": "",
        "character_profiles": json.dumps(
            record.get("character_profiles", []), ensure_ascii=False
        )[:6000],
        # for adaptive / decision / final
        "quiz_history": json.dumps(record.get("quiz_history", []), ensure_ascii=False),
        "winning_character_name": record.get("winning_character_name", ""),
        "max_total_questions": record.get("max_total_questions", 20),
        "min_questions_before_finish": record.get("min_questions_before_finish", 6),
        "confidence_threshold": record.get("confidence_threshold", 0.9),
        # INSTRUMENT RIGOR: the conditional block prod injects for validated
        # instruments ("" otherwise), plus the raw fields the deterministic
        # instrument checks read from check_ctx.
        "instrument_rigor": _instrument_rigor_for(function, category, record),
        "instrument_dimensions": record.get("instrument_dimensions") or [],
        "asked_dimensions": record.get("asked_dimensions") or [],
    }
    return ctx
