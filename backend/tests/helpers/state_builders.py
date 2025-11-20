# backend/tests/helpers/state_builders.py

"""
backend/tests/helpers/state_builders.py

Small, opinionated builders for agent GraphState dicts used in tests.

Why dicts?
- The API layer and graph both tolerate either Pydantic models *or* plain dicts
  in the state. Returning plain dicts keeps usage simple in fixtures and avoids
  accidental reliance on .model_dump() in tests.

What these build:
- make_synopsis_state(...)   -> state after /quiz/start (synopsis [+ optional characters])
- make_questions_state(...)  -> state after /quiz/proceed (baseline questions ready)
- make_finished_state(...)   -> state with a final result (what /quiz/status serves)

All builders:
- Accept either UUID or str for quiz_id; generate one if omitted.
- Accept simple Python dicts or Pydantic instances for content payloads.
- Allow overrides via **extras to tweak any state key for a specific test.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from langchain_core.messages import HumanMessage
from app.agent.state import GraphState
from app.agent.schemas import Synopsis as AgentSynopsis, QuizQuestion
from app.models.api import FinalResult as APIFinalResult


UUIDish = Union[str, uuid.UUID]


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _uuid_str(v: Optional[UUIDish] = None) -> str:
    if v is None:
        return str(uuid.uuid4())
    return str(v)


def _as_synopsis(obj: Optional[Union[Dict[str, Any], AgentSynopsis]] = None, *, category: str = "Gilmore Girls") -> Dict[str, Any]:
    """Return a plain dict synopsis (title, summary)."""
    if obj is None:
        # Default shape mirroring app.agent.schemas.Synopsis
        return {
            "title": f"Quiz: {category}",
            "summary": "A cozy tour through Stars Hollow personalities."
        }
    
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    
    if isinstance(obj, dict):
        # normalize common alias keys, just in case
        title = obj.get("title") or obj.get("name") or f"Quiz: {category}"
        summary = obj.get("summary") or obj.get("synopsis") or obj.get("synopsis_text") or ""
        return {"title": str(title), "summary": str(summary)}
    
    # last resort
    return {"title": f"Quiz: {category}", "summary": str(obj)}


def _as_question(obj: Union[str, Dict[str, Any], QuizQuestion]) -> Dict[str, Any]:
    """
    Coerce into **state-shaped** QuizQuestion dict:
      { "question_text": str, "options": [{"text": "...", "image_url": Optional[str]}] }
    """
    if isinstance(obj, str):
        return {"question_text": obj, "options": [{"text": "Yes"}, {"text": "No"}]}

    if hasattr(obj, "model_dump"):
        data = obj.model_dump()
    elif isinstance(obj, dict):
        data = dict(obj)
    else:  # unknown -> treat as bare text
        return {"question_text": str(obj), "options": [{"text": "Yes"}, {"text": "No"}]}

    # normalize keys
    qtext = data.get("question_text") or data.get("text") or "Question"
    raw_opts = data.get("options") or []
    opts: List[Dict[str, Any]] = []
    
    for o in raw_opts:
        if isinstance(o, str):
            opts.append({"text": o})
        elif isinstance(o, dict):
            t = o.get("text") or o.get("label") or str(o)
            img = o.get("image_url") or o.get("imageUrl")
            opt: Dict[str, Any] = {"text": str(t)}
            if img:
                opt["image_url"] = img
            opts.append(opt)
        elif hasattr(o, "model_dump"):
            od = o.model_dump()
            t = od.get("text") or od.get("label") or str(od)
            img = od.get("image_url") or od.get("imageUrl")
            opt = {"text": str(t)}
            if img:
                opt["image_url"] = img
            opts.append(opt)
        else:
            opts.append({"text": str(o)})

    # ensure at least 2 options (API expects >=2)
    if len(opts) < 2:
        opts = [{"text": "Yes"}, {"text": "No"}]

    return {"question_text": str(qtext), "options": opts}


def _as_questions(items: Optional[Iterable[Union[str, Dict[str, Any], QuizQuestion]]] = None) -> List[Dict[str, Any]]:
    """Coerce a list of question-like things into state-shaped question dicts."""
    if not items:
        return [
            _as_question({
                "question_text": "Which morning routine sounds most like you?",
                "options": [
                    {"text": "Coffee before talk"},
                    {"text": "Jog + podcast"},
                    {"text": "Make a to-do list"},
                    {"text": "Sleep in, then cram"},
                ],
            }),
            _as_question({
                "question_text": "Pick a Friday night plan.",
                "options": [
                    {"text": "Movie marathon"},
                    {"text": "Town diner hangout"},
                    {"text": "Study group"},
                    {"text": "Local event"},
                ],
            }),
        ]
    return [_as_question(q) for q in items]


def _as_result(obj: Optional[Union[Dict[str, Any], APIFinalResult]] = None) -> Dict[str, Any]:
    """Return a plain dict FinalResult (title, description, image_url?)."""
    if obj is None:
        # Default shape mirroring app.models.api.FinalResult
        return {
            "title": "You’re Lorelai Gilmore",
            "description": "Witty, warm, and fueled by coffee. You lead with charm and heart.",
            "image_url": "https://example.com/lorelai.png",
        }
        
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
        
    if isinstance(obj, dict):
        return {
            "title": str(obj.get("title") or "Your Result"),
            "description": str(obj.get("description") or ""),
            "image_url": obj.get("image_url") or obj.get("imageUrl"),
        }
        
    # last resort
    return {"title": "Your Result", "description": str(obj), "image_url": None}


def _history_from_answers(
    questions: Sequence[Dict[str, Any]],
    answers: Optional[Sequence[Union[int, Tuple[int, str], str]]] = None,
) -> List[Dict[str, Any]]:
    """
    Build quiz_history entries from answers.

    answers: sequence where each item is ONE of:
      - int            -> option index (0..N-1)
      - (idx, text)    -> (option_index, answer_text)
      - str            -> freeform answer_text (option_index=None)

    Returns list of dict entries: {question_index, question_text, answer_text, option_index?}
    """
    out: List[Dict[str, Any]] = []
    if not answers:
        return out

    for i, raw in enumerate(answers):
        if i >= len(questions):
            break # Avoid index error if more answers than questions
            
        q = questions[i]
        qtext = q.get("question_text", "")
        opts = q.get("options") or []
        
        if isinstance(raw, int):
            idx = int(raw)
            text = str(opts[idx].get("text")) if 0 <= idx < len(opts) else ""
            out.append({"question_index": i, "question_text": qtext, "answer_text": text, "option_index": idx})
        elif isinstance(raw, tuple):
            idx, text = raw
            out.append({"question_index": i, "question_text": qtext, "answer_text": str(text), "option_index": int(idx)})
        else:  # str
            out.append({"question_index": i, "question_text": qtext, "answer_text": str(raw), "option_index": None})
    return out


def _base_state(category: str, quiz_id: Optional[UUIDish], trace_id: Optional[str]) -> GraphState:
    """Common skeleton aligned with /quiz/start initial state keys."""
    sid = uuid.UUID(_uuid_str(quiz_id))
    tid = trace_id or _uuid_str()
    return {
        "session_id": sid,
        "trace_id": tid,
        "category": category,
        "messages": [HumanMessage(content=category)],
        "error_count": 0,
        "error_message": None,
        "is_error": False,
        "rag_context": None,
        "synopsis": None,  # Corrected key from category_synopsis
        "agent_plan": None, # Added key
        "ideal_archetypes": [],
        "generated_characters": [],
        "generated_questions": [],
        "quiz_history": [],
        "baseline_count": 0,
        "baseline_ready": False,
        "ready_for_questions": False,
        "final_result": None,
        "last_served_index": None,
    }


# ---------------------------------------------------------------------------
# Public builders
# ---------------------------------------------------------------------------

def make_synopsis_state(
    *,
    category: str = "Gilmore Girls",
    quiz_id: Optional[UUIDish] = None,
    trace_id: Optional[str] = None,
    synopsis: Optional[Union[Dict[str, Any], AgentSynopsis]] = None,
    characters: Optional[Iterable[Dict[str, Any]]] = None,
    **extras: Any,
) -> GraphState:
    """
    State snapshot right after /quiz/start:
    - has synopsis
    - may have generated_characters (optional)
    - ready_for_questions is False (gate remains closed)
    """
    state = _base_state(category, quiz_id, trace_id)
    
    # Normalize synopsis
    syn_dict = _as_synopsis(synopsis, category=category)
    state["synopsis"] = syn_dict
    
    # Default agent_plan based on synopsis if not provided in extras
    if "agent_plan" not in extras:
        state["agent_plan"] = {
            "title": syn_dict["title"],
            "synopsis": syn_dict["summary"],
            "ideal_archetypes": []
        }

    if characters:
        # character dict shape is loose; tests do not require strict fields here
        state["generated_characters"] = list(characters)
        
    state.update(extras)
    return state


def make_questions_state(
    *,
    category: str = "Gilmore Girls",
    quiz_id: Optional[UUIDish] = None,
    trace_id: Optional[str] = None,
    questions: Optional[Iterable[Union[str, Dict[str, Any], QuizQuestion]]] = None,
    baseline_count: Optional[int] = None,
    answers: Optional[Sequence[Union[int, Tuple[int, str], str]]] = None,
    synopsis: Optional[Union[Dict[str, Any], AgentSynopsis]] = None,
    **extras: Any,
) -> GraphState:
    """
    State snapshot after /quiz/proceed, with baseline questions prepared.

    Args:
      questions: list of question-like items; default to two sample questions.
      baseline_count: explicit baseline count (defaults to len(questions)).
      answers: if provided, creates quiz_history for first len(answers) questions.
               Each entry can be int (option index), str (freeform), or (idx, text).
    """
    state = _base_state(category, quiz_id, trace_id)
    qs = _as_questions(questions)
    
    state["synopsis"] = _as_synopsis(synopsis, category=category)
    state["generated_questions"] = qs
    state["baseline_count"] = int(baseline_count) if isinstance(baseline_count, int) else len(qs)
    state["baseline_ready"] = True
    state["ready_for_questions"] = True  # proceed opened the gate
    state["quiz_history"] = _history_from_answers(qs, answers)
    
    state.update(extras)
    return state


def make_finished_state(
    *,
    category: str = "Gilmore Girls",
    quiz_id: Optional[UUIDish] = None,
    trace_id: Optional[str] = None,
    result: Optional[Union[Dict[str, Any], APIFinalResult]] = None,
    **extras: Any,
) -> GraphState:
    """
    State snapshot when the quiz is finished (what /quiz/status returns as 'finished').
    Only 'final_result' is required by the endpoint; other keys are present but inert.
    """
    state = _base_state(category, quiz_id, trace_id)
    state["synopsis"] = _as_synopsis(None, category=category)
    state["final_result"] = _as_result(result)
    
    # Observability (optional): pretend we served the last baseline index
    if state.get("baseline_count", 0) > 0:
        state["last_served_index"] = int(state["baseline_count"]) - 1
        
    state.update(extras)
    return state


__all__ = [
    "make_synopsis_state",
    "make_questions_state",
    "make_finished_state",
]