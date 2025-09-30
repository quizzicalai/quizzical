"""
backend/tests/helpers/sample_payloads.py

Convenience factories for request payloads & query params used by integration,
unit, and smoke tests. These return plain dicts ready to pass to httpx/AsyncClient
(e.g., `client.post("/quiz/start", json=start_quiz_payload(...))`).

Design notes
------------
- Accept both str and UUID for `quiz_id`; always serialize to string.
- Hide API aliasing details (e.g., 'cf-turnstile-response' on /quiz/start).
- Keep function names/params aligned with the original test plan:
    * start_quiz_payload(topic="...")
    * next_question_payload(quiz_id, index, option_idx=None, freeform=None)
- Provide a couple of extra helpers commonly needed by the suite:
    * proceed_payload(quiz_id)
    * status_params(known_questions_count=0)
    * feedback_payload(quiz_id, rating="up"|"down", text=None)
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Union
from uuid import UUID


def _uuid_str(value: Union[str, UUID]) -> str:
    if isinstance(value, UUID):
        return str(value)
    return str(value)


# ---------------------------------------------------------------------------
# /quiz/start
# ---------------------------------------------------------------------------

def start_quiz_payload(
    topic: str = "Gilmore Girls",
    *,
    turnstile_token: str = "ok-turnstile",
) -> Dict[str, Any]:
    """
    Build a payload for POST /quiz/start.

    The server expects:
      {
        "category": "...",
        "cf-turnstile-response": "..."
      }
    """
    return {
        "category": topic,
        "cf-turnstile-response": turnstile_token,  # alias required by API model
    }


# ---------------------------------------------------------------------------
# /quiz/proceed
# ---------------------------------------------------------------------------

def proceed_payload(quiz_id: Union[str, UUID]) -> Dict[str, Any]:
    """Build a payload for POST /quiz/proceed."""
    return {"quiz_id": _uuid_str(quiz_id)}


# ---------------------------------------------------------------------------
# /quiz/next
# ---------------------------------------------------------------------------

def next_question_payload(
    quiz_id: Union[str, UUID],
    index: int,
    option_idx: Optional[int] = None,
    freeform: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build a payload for POST /quiz/next.

    The server accepts either:
      - option_index (int) to pick a multiple-choice option, OR
      - answer (str) for freeform (rare), but *at least one* must be provided.

    Args:
      quiz_id: UUID or string
      index: zero-based question index the client is answering
      option_idx: which option was chosen (0..N-1)
      freeform: arbitrary text answer

    Returns:
      dict with keys: quiz_id, question_index, option_index?, answer?
    """
    if option_idx is None and (freeform is None or freeform.strip() == ""):
        # Keep tests explicit—make an intentional choice of option or freeform.
        raise ValueError("next_question_payload requires either option_idx or freeform.")

    payload: Dict[str, Any] = {
        "quiz_id": _uuid_str(quiz_id),
        "question_index": int(index),
    }
    if option_idx is not None:
        payload["option_index"] = int(option_idx)
    if freeform is not None:
        payload["answer"] = str(freeform)
    return payload


# ---------------------------------------------------------------------------
# /quiz/status
# ---------------------------------------------------------------------------

def status_params(*, known_questions_count: int = 0) -> Dict[str, Any]:
    """
    Query params for GET /quiz/status/{quiz_id}.

    Example:
      client.get(f"/quiz/status/{quiz_id}", params=status_params(known_questions_count=1))
    """
    return {"known_questions_count": int(known_questions_count)}


# ---------------------------------------------------------------------------
# /feedback
# ---------------------------------------------------------------------------

def feedback_payload(
    quiz_id: Union[str, UUID],
    *,
    rating: str = "up",
    text: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build a payload for POST /feedback.

    rating must be "up" or "down" per FeedbackRatingEnum.
    """
    if rating not in {"up", "down"}:
        raise ValueError("rating must be 'up' or 'down'")
    payload: Dict[str, Any] = {
        "quiz_id": _uuid_str(quiz_id),
        "rating": rating,
    }
    if text is not None:
        payload["text"] = text
    return payload


__all__ = [
    "start_quiz_payload",
    "proceed_payload",
    "next_question_payload",
    "status_params",
    "feedback_payload",
]
