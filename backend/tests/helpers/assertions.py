"""
backend/tests/helpers/assertions.py

Small, friendly assertion helpers used across integration, unit, and smoke tests.

Design goals
------------
- Accept both plain dicts and Pydantic-like objects (with .model_dump() / .dict()).
- Raise crisp AssertionError messages that tell you exactly what failed.
- Return normalized dicts so tests can chain work (e.g., pass validated question/result forward).
- Be tolerant of minor key aliasing (e.g., question_text vs text; image_url vs imageUrl).

Available helpers
-----------------
- assert_is_uuid(value, version=4) -> str
- assert_synopsis_shape(obj) -> dict
- assert_question_shape(obj, *, min_options=2, max_options=None, allow_image_url=True) -> dict
- assert_questions_list(items, *, min_len=1, **question_kwargs) -> list[dict]
- assert_result_shape(obj, *, allow_empty_description=False) -> dict
- assert_graph_state_minimal(state) -> dict
- assert_graph_state_phase(state, *, phase) -> dict
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Literal, Optional, Tuple, Union
import uuid


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _as_dict(obj: Any) -> Dict[str, Any]:
    """Best-effort convert Pydantic-like or object to a plain dict."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    # Pydantic v2
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()  # type: ignore[attr-defined]
        except Exception:
            pass
    # Pydantic v1 / dataclass-ish
    if hasattr(obj, "dict"):
        try:
            return obj.dict()  # type: ignore[attr-defined]
        except Exception:
            pass
    # Fallback to vars() when it's a simple object
    try:
        m = dict(vars(obj))
        if m:
            return m
    except Exception:
        pass
    # Ultimate fallback: single-value dict so callers can read something
    return {"value": obj}


def _get_str(d: Dict[str, Any], *keys: str, default: Optional[str] = None) -> Optional[str]:
    """Fetch first present key as str (trimmed)."""
    for k in keys:
        if k in d and d[k] is not None:
            v = str(d[k]).strip()
            return v
    return default


def _get_list(d: Dict[str, Any], *keys: str) -> Optional[List[Any]]:
    for k in keys:
        v = d.get(k, None)
        if isinstance(v, list):
            return v
    return None


def _normalize_option(item: Any) -> Dict[str, Any]:
    """Normalize a single answer option to {'text': str, 'image_url'?: str}."""
    d = _as_dict(item)
    text = _get_str(d, "text", "label")
    if not text:
        raise AssertionError(f"Option missing required 'text'/'label': got {d!r}")
    # image_url may be snake_case or camelCase; if truthy, keep
    img = _get_str(d, "image_url", "imageUrl", default=None)
    out: Dict[str, Any] = {"text": text}
    if img:
        out["image_url"] = img
    return out


def _ensure_bool(value: Any, key: str) -> bool:
    if isinstance(value, bool):
        return value
    raise AssertionError(f"Expected '{key}' to be a boolean, got {type(value).__name__}: {value!r}")


# ---------------------------------------------------------------------------
# Public assertions
# ---------------------------------------------------------------------------

def assert_is_uuid(value: Union[str, uuid.UUID], *, version: int = 4) -> str:
    """
    Assert that `value` is a valid UUID (default: v4).
    Returns the canonical string form.
    """
    if isinstance(value, uuid.UUID):
        if version and value.version != version:
            raise AssertionError(f"Expected UUID version {version}, got v{value.version}: {value}")
        return str(value)

    if not isinstance(value, str):
        raise AssertionError(f"Expected UUID as str/UUID, got {type(value).__name__}: {value!r}")

    try:
        u = uuid.UUID(value)
    except Exception:
        raise AssertionError(f"Invalid UUID string: {value!r}")
    if version and u.version != version:
        raise AssertionError(f"Expected UUID version {version}, got v{u.version}: {value}")
    return str(u)


def assert_synopsis_shape(obj: Any) -> Dict[str, Any]:
    """
    Validate a Synopsis-like object with required keys:
      - title: non-empty string
      - summary: string (may be empty per agent.schemas)
    Returns normalized {'title': str, 'summary': str}.
    """
    d = _as_dict(obj)
    title = _get_str(d, "title")
    if not title:
        raise AssertionError(f"Synopsis missing non-empty 'title': {d!r}")
    summary = _get_str(d, "summary", "synopsis", "synopsis_text", default="")
    if summary is None:
        summary = ""
    if not isinstance(summary, str):
        raise AssertionError(f"Synopsis 'summary' must be a string; got {type(summary).__name__}")
    return {"title": title, "summary": summary}


def assert_question_shape(
    obj: Any,
    *,
    min_options: int = 2,
    max_options: Optional[int] = None,
    allow_image_url: bool = True,
) -> Dict[str, Any]:
    """
    Validate a QuizQuestion-like object.

    Accepts BOTH agent-state shape and FE-shape:
      - agent-state: {'question_text': str, 'options': [{'text': str, 'image_url'?: str}]}
      - FE active:  {'text': str, 'options': [{'text': str, 'image_url'?: str}]}

    Returns normalized:
      {'question_text': str, 'options': [{'text': str, 'image_url'?: str}, ...]}
    """
    d = _as_dict(obj)
    text = _get_str(d, "question_text", "text")
    if not text:
        raise AssertionError(f"Question missing 'question_text'/'text': {d!r}")

    raw_opts = _get_list(d, "options")
    if raw_opts is None:
        raise AssertionError(f"Question missing 'options' list: {d!r}")
    if not isinstance(raw_opts, list):
        raise AssertionError(f"'options' must be a list, got {type(raw_opts).__name__}: {raw_opts!r}")

    options: List[Dict[str, Any]] = []
    for i, opt in enumerate(raw_opts):
        try:
            norm = _normalize_option(opt)
            if not allow_image_url and "image_url" in norm:
                # If images are not allowed in this context, strip them to keep comparison stable
                norm.pop("image_url", None)
            options.append(norm)
        except AssertionError as e:
            raise AssertionError(f"Invalid option at index {i}: {e}")

    if len(options) < min_options:
        raise AssertionError(f"Question has too few options: {len(options)} < {min_options}")
    if max_options is not None and len(options) > max_options:
        raise AssertionError(f"Question has too many options: {len(options)} > {max_options}")

    return {"question_text": text, "options": options}


def assert_questions_list(
    items: Any,
    *,
    min_len: int = 1,
    **question_kwargs: Any,
) -> List[Dict[str, Any]]:
    """
    Validate a list of questions. Each element is validated with assert_question_shape(**question_kwargs).
    Returns a list of normalized question dicts.
    """
    if not isinstance(items, list):
        raise AssertionError(f"Expected a list of questions, got {type(items).__name__}: {items!r}")
    if len(items) < min_len:
        raise AssertionError(f"Expected at least {min_len} question(s), got {len(items)}")
    normalized: List[Dict[str, Any]] = []
    for i, q in enumerate(items):
        try:
            normalized.append(assert_question_shape(q, **question_kwargs))
        except AssertionError as e:
            raise AssertionError(f"Question list validation failed at index {i}: {e}")
    return normalized


def assert_result_shape(obj: Any, *, allow_empty_description: bool = False) -> Dict[str, Any]:
    """
    Validate a FinalResult-like object: {'title': str, 'description': str, 'image_url'?: str|None}
    Returns normalized dict with only the relevant keys.
    """
    d = _as_dict(obj)

    title = _get_str(d, "title")
    if not title:
        raise AssertionError(f"Result missing non-empty 'title': {d!r}")

    desc = _get_str(d, "description", "summary")
    if desc is None:
        raise AssertionError("Result missing 'description' (or 'summary').")
    if not allow_empty_description and not desc:
        raise AssertionError("Result 'description' must not be empty.")
    # In tolerant mode we still ensure it's a string
    if not isinstance(desc, str):
        raise AssertionError(f"Result 'description' must be a string; got {type(desc).__name__}")

    image_url = _get_str(d, "image_url", "imageUrl", default=None)
    if image_url is not None and not isinstance(image_url, str):
        raise AssertionError(f"Result 'image_url' must be a string if present; got {type(image_url).__name__}")

    out: Dict[str, Any] = {"title": title, "description": desc}
    if image_url:
        out["image_url"] = image_url
    return out


def assert_graph_state_minimal(state: Any) -> Dict[str, Any]:
    """
    Validate a minimal subset of agent GraphState that many tests rely on.

    Required keys:
      - session_id: UUID (str or uuid.UUID)
      - trace_id: non-empty string
      - category: non-empty string

    Optional-but-typed when present:
      - messages: list
      - generated_questions: list
      - quiz_history: list
      - baseline_count: int >= 0
      - baseline_ready: bool
      - ready_for_questions: bool
    """
    d = _as_dict(state)

    # required
    sid = d.get("session_id")
    if sid is None:
        raise AssertionError("GraphState missing 'session_id'.")
    assert_is_uuid(sid)

    trace_id = _get_str(d, "trace_id")
    if not trace_id:
        raise AssertionError("GraphState missing non-empty 'trace_id'.")

    category = _get_str(d, "category")
    if not category:
        raise AssertionError("GraphState missing non-empty 'category'.")

    # optional (type-checked if present)
    if "messages" in d and not isinstance(d["messages"], list):
        raise AssertionError(f"'messages' must be a list when present; got {type(d['messages']).__name__}")
    if "generated_questions" in d and not isinstance(d["generated_questions"], list):
        raise AssertionError(f"'generated_questions' must be a list when present; got {type(d['generated_questions']).__name__}")
    if "quiz_history" in d and not isinstance(d["quiz_history"], list):
        raise AssertionError(f"'quiz_history' must be a list when present; got {type(d['quiz_history']).__name__}")
    if "baseline_count" in d:
        bc = d["baseline_count"]
        if not isinstance(bc, int) or bc < 0:
            raise AssertionError(f"'baseline_count' must be int >= 0; got {bc!r}")
    if "baseline_ready" in d:
        _ensure_bool(d["baseline_ready"], "baseline_ready")
    if "ready_for_questions" in d:
        _ensure_bool(d["ready_for_questions"], "ready_for_questions")

    return d


def assert_graph_state_phase(
    state: Any,
    *,
    phase: Literal["prep", "baseline", "adaptive", "finished"],
) -> Dict[str, Any]:
    """
    Validate phase-specific invariants for the agent state.

    - prep:
        * has category_synopsis with a non-empty title
    - baseline:
        * baseline_ready True
        * baseline_count == len(generated_questions)
    - adaptive:
        * baseline_ready True
        * len(quiz_history) >= baseline_count
        * (should_finalize is not True)
    - finished:
        * final_result present with valid shape
    """
    d = assert_graph_state_minimal(state)

    if phase == "prep":
        syn = d.get("category_synopsis")
        if not syn:
            raise AssertionError("Expected 'category_synopsis' in prep phase.")
        _ = assert_synopsis_shape(syn)
        return d

    if phase == "baseline":
        if not d.get("baseline_ready"):
            raise AssertionError("Expected 'baseline_ready' to be True in baseline phase.")
        gq = d.get("generated_questions", []) or []
        bc = d.get("baseline_count", None)
        if not isinstance(bc, int):
            raise AssertionError("Expected integer 'baseline_count' in baseline phase.")
        if bc != len(gq):
            raise AssertionError(f"baseline_count mismatch: expected {bc}, questions={len(gq)}")
        # Validate question shapes lightly
        _ = assert_questions_list(gq, min_len=0)
        return d

    if phase == "adaptive":
        if not d.get("baseline_ready"):
            raise AssertionError("Adaptive phase requires 'baseline_ready' True.")
        bc = int(d.get("baseline_count") or 0)
        hist = d.get("quiz_history", []) or []
        if not isinstance(hist, list):
            raise AssertionError("'quiz_history' must be a list in adaptive phase.")
        if len(hist) < bc:
            raise AssertionError(f"Adaptive phase requires answers >= baseline_count; got {len(hist)} < {bc}")
        if d.get("should_finalize") is True:
            raise AssertionError("Adaptive phase should not have 'should_finalize' set True; that's finished.")
        return d

    if phase == "finished":
        res = d.get("final_result")
        if not res:
            raise AssertionError("Finished phase requires 'final_result'.")
        _ = assert_result_shape(res)
        return d

    raise AssertionError(f"Unknown phase: {phase!r}")


__all__ = [
    "assert_is_uuid",
    "assert_synopsis_shape",
    "assert_question_shape",
    "assert_questions_list",
    "assert_result_shape",
    "assert_graph_state_minimal",
    "assert_graph_state_phase",
]
