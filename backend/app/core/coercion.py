"""§19.2 AC-QUALITY-R2-COERCE — canonical Pydantic/dict coercion helper.

Centralises the repeated "is it a model? is it a dict? is it None?" pattern
that previously lived (slightly differently) in `quiz.py`, `graph.py`, and
`llm_helpers.py`. Returning a single source of truth means bug fixes apply
in exactly one place and behaviour is uniform across the agent surface.
"""
from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def coerce_to_dict(obj: Any) -> dict[str, Any]:
    """Reduce a Pydantic model / dict / None / dict-like to a plain dict.

    Behaviour (AC-QUALITY-R2-COERCE-2):
      * ``None``            → ``{}``
      * ``dict``            → shallow copy
      * has ``model_dump``  → its dump (Pydantic v2)
      * has ``dict``        → its dict() (Pydantic v1 fallback)
      * anything else       → ``TypeError`` (loud, not silent)

    Failures inside ``model_dump`` are logged at debug level and replaced
    with an empty dict (AC-QUALITY-R2-COERCE-3) — callers that need stricter
    handling should validate the model before calling this helper.
    """
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return dict(obj)
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        try:
            result = dump()
        except Exception as exc:  # pragma: no cover - exercised by tests
            logger.debug(
                "coercion.model_dump.fail",
                error=str(exc),
                obj_type=type(obj).__name__,
            )
            return {}
        if not isinstance(result, dict):
            logger.debug(
                "coercion.model_dump.non_dict",
                obj_type=type(obj).__name__,
                result_type=type(result).__name__,
            )
            return {}
        return result
    legacy = getattr(obj, "dict", None)
    if callable(legacy):
        try:
            result = legacy()
        except Exception as exc:  # pragma: no cover - exercised by tests
            logger.debug(
                "coercion.legacy_dict.fail",
                error=str(exc),
                obj_type=type(obj).__name__,
            )
            return {}
        if isinstance(result, dict):
            return result
    raise TypeError(
        f"coerce_to_dict: cannot reduce {type(obj).__name__} to dict"
    )


__all__ = ["coerce_to_dict"]
