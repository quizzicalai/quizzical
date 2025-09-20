# backend/app/services/state_hydration.py
"""
State Hydration Utilities

Purpose
-------
When we cache the agent's state (e.g., in Redis), Pydantic models become plain
dicts/lists. Several graph nodes rely on attribute access like `c.name`
instead of `c["name"]`. This module *rehydrates* those dicts back into the
agent-side Pydantic models defined in `app.agent.state`.

Scope
-----
- Rehydrate:
    * Synopsis
    * CharacterProfile
    * QuizQuestion
- Be resilient to malformed or partially missing data.
- Never raise from hydration; log and fall back to a safe shape.

Usage
-----
from app.services.state_hydration import hydrate_graph_state
state = hydrate_graph_state(state)

Design Notes
------------
- We prefer `model_validate()` (Pydantic v2) for strict hydration.
- If validation fails, we do a best-effort "duck-typed" fallback that ensures
  downstream code can rely on attribute access for the fields nodes use.
- Logging is verbose at DEBUG and concise at WARNING/ERROR for operational use.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

from pydantic import ValidationError

# Import agent-side models we want to rehydrate into.
from app.agent.state import Synopsis, CharacterProfile, QuizQuestion

logger = logging.getLogger(__name__)

__all__ = [
    "hydrate_graph_state",
]


# ---------------------------
# Helpers: shape coercion
# ---------------------------

def _safe_str(value: Any, default: str = "") -> str:
    """Coerce any value to `str` safely (avoids None -> 'None' surprises by defaulting)."""
    if value is None:
        return default
    try:
        return str(value)
    except Exception:
        return default


def _ensure_list(value: Any) -> List[Any]:
    """Return value if list-like, else empty list."""
    if isinstance(value, list):
        return value
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


# ---------------------------
# Model-specific coercers
# ---------------------------

def _as_synopsis(x: Any) -> Optional[Synopsis]:
    """Coerce `x` to a `Synopsis` or return None (logging on failure)."""
    if x is None:
        return None
    if isinstance(x, Synopsis):
        return x

    try:
        return Synopsis.model_validate(x)
    except ValidationError as ve:
        logger.warning(
            "Failed to validate Synopsis; dropping to None",
            extra={"error": str(ve)}
        )
        return None
    except Exception as exc:
        logger.error(
            "Unexpected error hydrating Synopsis; dropping to None",
            extra={"error": str(exc), "type": type(exc).__name__}
        )
        return None


def _as_character(x: Any, index: Optional[int] = None) -> Optional[CharacterProfile]:
    """
    Coerce `x` to `CharacterProfile`. On failure, returns a *minimal* CharacterProfile
    that won't crash downstream (with empty strings) and logs the problem.
    """
    if isinstance(x, CharacterProfile):
        return x

    # Structured validation first.
    try:
        return CharacterProfile.model_validate(x)
    except ValidationError as ve:
        # Duck-typed fallback; do not fail the whole node because one entry is malformed.
        try:
            # x might be dict-like; handle attribute access as well.
            get = (x.get if isinstance(x, dict) else lambda k, d=None: getattr(x, k, d))

            name = _safe_str(get("name", ""), "")
            short_description = _safe_str(get("short_description", ""), "")
            profile_text = _safe_str(get("profile_text", ""), "")
            image_url = get("image_url", None)

            fallback = CharacterProfile(
                name=name,
                short_description=short_description,
                profile_text=profile_text,
                image_url=image_url if (image_url is None or isinstance(image_url, str)) else None,
            )
            logger.warning(
                "CharacterProfile validation failed; using fallback",
                extra={
                    "index": index,
                    "error": str(ve),
                    "fallback_name": fallback.name,
                },
            )
            return fallback
        except Exception as exc:
            logger.error(
                "Unexpected error building CharacterProfile fallback; entry skipped",
                extra={"index": index, "error": str(exc), "type": type(exc).__name__},
            )
            return None
    except Exception as exc:
        logger.error(
            "Unexpected error hydrating CharacterProfile; entry skipped",
            extra={"index": index, "error": str(exc), "type": type(exc).__name__},
        )
        return None


def _normalize_question_options(raw_options: Any, q_index: Optional[int]) -> List[Dict[str, str]]:
    """
    Normalize `options` field into `List[Dict[str, str]]` with at least a 'text' key.
    Unknown keys are ignored except 'image_url' which we keep if it's a string.
    """
    options_in = _ensure_list(raw_options)
    normalized: List[Dict[str, str]] = []

    for i, opt in enumerate(options_in):
        if isinstance(opt, dict):
            text = _safe_str(opt.get("text", ""), "")
            entry: Dict[str, str] = {"text": text}
            image_url = opt.get("image_url")
            if isinstance(image_url, str):
                entry["image_url"] = image_url
            normalized.append(entry)
        else:
            # Allow scalar options (e.g., "A", 1) by turning them into {"text": "A"}
            normalized.append({"text": _safe_str(opt, "")})
            logger.debug(
                "Coerced non-dict option to dict",
                extra={"question_index": q_index, "option_index": i, "repr": repr(opt)},
            )

    return normalized


def _as_question(x: Any, index: Optional[int] = None) -> Optional[QuizQuestion]:
    """
    Coerce `x` to `QuizQuestion`. On failure, returns a *minimal* QuizQuestion
    (empty text, empty options) and logs the problem.
    """
    if isinstance(x, QuizQuestion):
        return x

    # Try structured validation first.
    try:
        return QuizQuestion.model_validate(x)
    except ValidationError as ve:
        try:
            get = (x.get if isinstance(x, dict) else lambda k, d=None: getattr(x, k, d))
            text = _safe_str(get("question_text", ""), "")
            options = _normalize_question_options(get("options", []), q_index=index)

            fallback = QuizQuestion(question_text=text, options=options)
            logger.warning(
                "QuizQuestion validation failed; using fallback",
                extra={
                    "index": index,
                    "error": str(ve),
                    "text_len": len(text or ""),
                    "options_count": len(options),
                },
            )
            return fallback
        except Exception as exc:
            logger.error(
                "Unexpected error building QuizQuestion fallback; entry skipped",
                extra={"index": index, "error": str(exc), "type": type(exc).__name__},
            )
            return None
    except Exception as exc:
        logger.error(
            "Unexpected error hydrating QuizQuestion; entry skipped",
            extra={"index": index, "error": str(exc), "type": type(exc).__name__},
        )
        return None


# ---------------------------
# Public API
# ---------------------------

def hydrate_graph_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Coerce cached dicts/lists within the graph state back into Pydantic models.

    This function NEVER raises; in worst case it returns the original `state`
    (shallow-copied) with best-effort fixes applied.

    Fields handled:
        - category_synopsis -> Synopsis | None
        - generated_characters -> List[CharacterProfile]
        - generated_questions -> List[QuizQuestion]

    Parameters
    ----------
    state : Dict[str, Any]
        The state dict loaded from cache or constructed upstream.

    Returns
    -------
    Dict[str, Any]
        A new dict with the same keys as `state`, with the above fields
        rehydrated into their Pydantic model types where possible.
    """
    if not isinstance(state, dict):
        logger.warning(
            "hydrate_graph_state called with non-dict; returning input unchanged",
            extra={"received_type": type(state).__name__},
        )
        return state  # type: ignore[return-value]

    # Shallow copy to avoid mutating caller's structure.
    s: Dict[str, Any] = dict(state)

    # Synopsis
    try:
        s["category_synopsis"] = _as_synopsis(s.get("category_synopsis"))
    except Exception as exc:
        logger.error(
            "Failed hydrating category_synopsis; leaving original value",
            extra={"error": str(exc), "type": type(exc).__name__},
        )

    # Characters
    try:
        raw_chars = s.get("generated_characters") or []
        chars_in = _ensure_list(raw_chars)
        hydrated_chars: List[CharacterProfile] = []
        skipped = 0

        for i, c in enumerate(chars_in):
            hc = _as_character(c, index=i)
            if hc is not None:
                hydrated_chars.append(hc)
            else:
                skipped += 1

        s["generated_characters"] = hydrated_chars

        logger.debug(
            "Hydrated generated_characters",
            extra={
                "input_count": len(chars_in),
                "output_count": len(hydrated_chars),
                "skipped": skipped,
            },
        )
    except Exception as exc:
        logger.error(
            "Failed hydrating generated_characters; leaving original value",
            extra={"error": str(exc), "type": type(exc).__name__},
        )

    # Questions
    try:
        raw_qs = s.get("generated_questions") or []
        qs_in = _ensure_list(raw_qs)
        hydrated_qs: List[QuizQuestion] = []
        skipped_q = 0

        for i, q in enumerate(qs_in):
            hq = _as_question(q, index=i)
            if hq is not None:
                hydrated_qs.append(hq)
            else:
                skipped_q += 1

        s["generated_questions"] = hydrated_qs

        logger.debug(
            "Hydrated generated_questions",
            extra={
                "input_count": len(qs_in),
                "output_count": len(hydrated_qs),
                "skipped": skipped_q,
            },
        )
    except Exception as exc:
        logger.error(
            "Failed hydrating generated_questions; leaving original value",
            extra={"error": str(exc), "type": type(exc).__name__},
        )

    return s
