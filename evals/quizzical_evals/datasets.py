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
    }.get(bucket, ("types", "whimsical", "identify"))


def assemble_context(function: str, record: dict[str, Any]) -> dict[str, Any]:
    """Build the format kwargs for a function's prompt from a dataset record.

    Returns a dict that is a superset of any single prompt's needs; ``str.format``
    on a template only consumes the keys it references.
    """
    category = record.get("category", "")
    bucket = record.get("bucket", "open")
    okind, cmode, intent = _kind_mode_intent(bucket)
    canonical = record.get("canonical_names") or []
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
        "count": record.get("count", 6),
        "min_characters": record.get("min_characters", 4),
        "max_characters": record.get("max_characters", 32),
        # for profile writers
        "character_names": record.get("character_names", canonical),
        "character_name": (record.get("character_names") or canonical or ["Outcome"])[0],
        "character_contexts": "(none)",
        "character_context": "(none)",
        "character_profiles": json.dumps(
            record.get("character_profiles", []), ensure_ascii=False
        )[:6000],
        # for adaptive / decision / final
        "quiz_history": json.dumps(record.get("quiz_history", []), ensure_ascii=False),
        "winning_character_name": record.get("winning_character_name", ""),
        "progress_phrase_pool": record.get("progress_phrase_pool", ""),
        "max_total_questions": record.get("max_total_questions", 20),
        "min_questions_before_finish": record.get("min_questions_before_finish", 6),
        "confidence_threshold": record.get("confidence_threshold", 0.9),
    }
    return ctx
