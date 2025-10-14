# backend/app/agent/tools/content_creation_tools.py
"""
Agent Tools: Content Creation (Zero-knowledge, Non-deterministic)

These tools create the content used by the quiz:
- category synopsis (title + summary)
- character/profile writeups (no canon validation; no retrieval; zero prior knowledge)
- baseline and adaptive questions
- final result

Alignment notes (2025-10 strategy):
- NO allow-list logic here; retrieval is not used in this module.
- We assume ZERO prior knowledge of any media/topic (no RAG, no canonical checks).
- We DO NOT perform disambiguation. If outputs are off, the user restarts.
- Prompts use {category} as the canonical placeholder and receive {intent} from analysis.
- Character options preserve image_url when present.

Implementation notes (structured outputs):
- All structured LLM calls flow through app.agent.llm_helpers.invoke_structured.
- We request **Pydantic/TypeAdapter** response models so the helper returns validated objects.
- No use of `schema_kwargs`/`model_cls` here; only `response_model` (or a TypeAdapter).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Iterable
import re

import structlog
from langchain_core.tools import tool
from pydantic import ValidationError
from pydantic import TypeAdapter

from app.agent.prompts import prompt_manager
from app.agent.state import CharacterProfile, QuizQuestion, Synopsis
from app.agent.schemas import QuestionOut, QuestionList, NextStepDecision
from app.models.api import FinalResult
from app.core.config import settings

# Topic/intent analysis (data-driven, no network)
from app.agent.tools.intent_classification import analyze_topic

# Centralized structured LLM invocation
from app.agent.llm_helpers import invoke_structured

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Topic analysis helpers
# ---------------------------------------------------------------------------

def _analyze_topic_safe(category: str, synopsis: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Call analyze_topic with best-effort signature compatibility.
    Some deployments take (category), newer ones accept (category, synopsis).
    """
    try:
        return analyze_topic(category, synopsis)
    except TypeError:
        return analyze_topic(category)


def _resolve_analysis(
    category: str,
    synopsis: Optional[Dict] = None,
    analysis: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Use provided analysis if valid; otherwise compute locally."""
    if isinstance(analysis, dict) and analysis.get("normalized_category"):
        return analysis
    return _analyze_topic_safe(category, synopsis)


# ---------------------------------------------------------------------------
# Options normalization (preserve image_url)
# ---------------------------------------------------------------------------

def _option_to_dict(opt: Any) -> Dict[str, Any]:
    """
    Coerce option (str | dict | pydantic | dataclass | object-with-text) → {'text', 'image_url'?}.
    Avoid using str(opt) to prevent repr leakage like "text='A' image_url=None".
    """
    if isinstance(opt, str):
        return {"text": opt.strip()}

    if isinstance(opt, dict):
        text = (opt.get("text") or opt.get("label") or opt.get("option") or "").strip()
        out: Dict[str, Any] = {"text": text}
        img = opt.get("image_url") or opt.get("imageUrl") or opt.get("image")
        if img:
            out["image_url"] = img
        return out

    if hasattr(opt, "model_dump"):
        data = opt.model_dump()
        text = str(data.get("text") or data.get("label") or "").strip()
        out = {"text": text}
        img = data.get("image_url") or data.get("imageUrl") or data.get("image")
        if img:
            out["image_url"] = img
        return out

    if hasattr(opt, "__dataclass_fields__"):
        text = str(getattr(opt, "text", getattr(opt, "label", ""))).strip()
        out = {"text": text}
        img = getattr(opt, "image_url", None) or getattr(opt, "imageUrl", None) or getattr(opt, "image", None)
        if img:
            out["image_url"] = img
        return out

    if hasattr(opt, "text") or hasattr(opt, "label"):
        text = str(getattr(opt, "text", getattr(opt, "label", ""))).strip()
        out = {"text": text}
        img = getattr(opt, "image_url", None) or getattr(opt, "imageUrl", None) or getattr(opt, "image", None)
        if img:
            out["image_url"] = img
        return out

    return {"text": str(opt).strip()}


def _norm_text_key(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().casefold()


def _normalize_options(raw: List[Any], max_options: Optional[int] = None) -> List[Dict[str, Any]]:
    """Coerce to [{'text', 'image_url'?}], dedupe by normalized text (case/space-insensitive), prefer keeping media."""
    coerced: List[Dict[str, Any]] = []
    for opt in (raw or []):
        d = _option_to_dict(opt)
        text = str(d.get("text") or "").strip()
        if not text:
            continue
        item: Dict[str, Any] = {"text": text}
        if d.get("image_url"):
            item["image_url"] = d["image_url"]
        coerced.append(item)

    seen: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for item in coerced:
        key = _norm_text_key(item["text"])
        if key not in seen:
            seen[key] = item
            order.append(key)
        else:
            existing = seen[key]
            # Upgrade with image_url if the later duplicate has one
            if not existing.get("image_url") and item.get("image_url"):
                existing["image_url"] = item["image_url"]

    uniq = [seen[k] for k in order]
    if max_options and max_options > 0:
        uniq = uniq[:max_options]
    return uniq


def _ensure_min_options(options: List[Dict[str, Any]], minimum: int = 2) -> List[Dict[str, Any]]:
    """
    Ensure each question has at least `minimum` options.
    - Filters out malformed entries (missing/blank 'text')
    - Omits falsy image_url values
    - Pads deterministically with generic choices
    """
    clean: List[Dict[str, Any]] = []
    for o in options or []:
        if not isinstance(o, dict):
            continue
        text = str(o.get("text") or "").strip()
        if not text:
            continue
        out: Dict[str, Any] = {"text": text}
        img = o.get("image_url")
        if isinstance(img, str) and img.strip():
            out["image_url"] = img.strip()
        clean.append(out)

    if len(clean) >= minimum:
        return clean

    fillers = [{"text": "Yes"}, {"text": "No"}, {"text": "Maybe"}, {"text": "Skip"}]
    need = max(0, minimum - len(clean))
    return clean + fillers[:need]


def _ensure_quiz_prefix(title: str) -> str:
    t = (title or "").strip()
    if not t:
        return "Quiz: Untitled"
    t = re.sub(r"(?i)^quiz\s*[:\-–—]\s*", "", t).strip()
    return f"Quiz: {t}"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@tool
async def generate_category_synopsis(
    category: str,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Synopsis:
    """Generate a synopsis (title + summary) for the quiz category (zero-knowledge)."""
    logger.info("tool.generate_category_synopsis.start", category=category)
    analysis = _resolve_analysis(category)
    prompt = prompt_manager.get_prompt("synopsis_generator")
    messages = prompt.invoke(
        {
            "category": analysis["normalized_category"],
            "outcome_kind": analysis["outcome_kind"],
            "creativity_mode": analysis["creativity_mode"],
            "intent": analysis.get("intent", "identify"),
            # back-compat (safe to include)
            "normalized_category": analysis["normalized_category"],
        }
    ).messages
    try:
        out: Synopsis = await invoke_structured(
            tool_name="synopsis_generator",
            messages=messages,
            response_model=Synopsis,
            trace_id=trace_id,
            session_id=session_id,
        )
        out.title = _ensure_quiz_prefix(out.title)
        logger.info("tool.generate_category_synopsis.ok", title=out.title)
        return out
    except Exception as e:
        logger.error("tool.generate_category_synopsis.fail", error=str(e), exc_info=True)
        return Synopsis(title=f"Quiz: {analysis['normalized_category']}", summary="")


@tool
async def draft_character_profiles(
    character_names: List[str],
    category: str,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
    analysis: Optional[Dict[str, Any]] = None,
) -> List[CharacterProfile]:
    """
    Draft profiles for multiple outcomes in one call.
    ZERO-KNOWLEDGE: We DO NOT fetch or validate externally. We provide creative, self-consistent bios.
    """
    logger.info(
        "tool.draft_character_profiles.start",
        category=category,
        count=len(character_names or []),
    )

    analysis = _resolve_analysis(category, None, analysis)
    count = len(character_names or [])
    if count <= 0:
        logger.info("tool.draft_character_profiles.noop", reason="empty_character_list")
        return []

    # No retrieval/context under the new strategy; just pass empty contexts.
    contexts: Dict[str, str] = {}

    prompt = prompt_manager.get_prompt("profile_batch_writer")
    messages = prompt.invoke(
        {
            "category": analysis["normalized_category"],
            "outcome_kind": analysis["outcome_kind"],
            "creativity_mode": analysis["creativity_mode"],
            "intent": analysis.get("intent", "identify"),
            "character_contexts": contexts,     # intentionally empty
            "character_names": character_names,
            "count": count,
        }
    ).messages

    try:
        adapter = TypeAdapter(List[CharacterProfile])  # on-wire schema + validation
        objs: List[CharacterProfile] = await invoke_structured(
            tool_name="profile_batch_writer",
            messages=messages,
            response_model=adapter,
            trace_id=trace_id,
            session_id=session_id,
        )
    except Exception as e:
        logger.error("tool.draft_character_profiles.validation_or_invoke_fail", error=str(e), exc_info=True)
        return []

    # Light name lock: keep requested labels where possible
    fixed: List[CharacterProfile] = []
    for want, got in zip(character_names or [], objs or []):
        try:
            if (got.name or "").strip().casefold() != (want or "").strip().casefold():
                fixed.append(
                    CharacterProfile(
                        name=want,
                        short_description=getattr(got, "short_description", "") or "",
                        profile_text=getattr(got, "profile_text", "") or "",
                        image_url=getattr(got, "image_url", None),
                    )
                )
            else:
                fixed.append(got)
        except Exception:
            fixed.append(CharacterProfile(name=want, short_description="", profile_text=""))

    logger.info("tool.draft_character_profiles.ok", returned=len(fixed))
    return fixed


@tool
async def draft_character_profile(
    character_name: str,
    category: str,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
    analysis: Optional[Dict[str, Any]] = None,
) -> CharacterProfile:
    """
    Draft a single profile. Under the new strategy we DO NOT assume canon or perform retrieval.
    Output is creative and self-consistent, guided only by the prompts and category.
    """
    logger.info("tool.draft_character_profile.start", character_name=character_name, category=category)
    analysis = _resolve_analysis(category, None, analysis)

    prompt = prompt_manager.get_prompt("profile_writer")
    messages = prompt.invoke(
        {
            "character_name": character_name,
            "category": analysis["normalized_category"],
            "outcome_kind": analysis["outcome_kind"],
            "creativity_mode": analysis["creativity_mode"],
            "intent": analysis.get("intent", "identify"),
            "character_context": "",  # intentionally empty under zero-knowledge strategy
            "normalized_category": analysis["normalized_category"],  # back-compat
        }
    ).messages
    try:
        out: CharacterProfile = await invoke_structured(
            tool_name="profile_writer",
            messages=messages,
            response_model=CharacterProfile,
            trace_id=trace_id,
            session_id=session_id,
        )
        if not getattr(out, "name", None):
            out.name = character_name
        logger.debug("tool.draft_character_profile.ok", character=out.name)
        return out
    except ValidationError as e:
        logger.error("tool.draft_character_profile.validation", error=str(e), exc_info=True)
        return CharacterProfile(name=character_name, short_description="", profile_text="")
    except Exception as e:
        logger.error("tool.draft_character_profile.fail", error=str(e), exc_info=True)
        return CharacterProfile(name=character_name, short_description="", profile_text="")


@tool
async def improve_character_profile(
    existing_profile: Dict,
    feedback: str,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> CharacterProfile:
    """Improve an existing profile using feedback (keeps name constant)."""
    logger.info("tool.improve_character_profile.start", name=existing_profile.get("name"))
    prompt = prompt_manager.get_prompt("profile_improver")
    messages = prompt.invoke({"existing_profile": existing_profile, "feedback": feedback}).messages
    try:
        out: CharacterProfile = await invoke_structured(
            tool_name="profile_improver",
            messages=messages,
            response_model=CharacterProfile,
            trace_id=trace_id,
            session_id=session_id,
        )
        logger.debug("tool.improve_character_profile.ok", name=out.name)
        return out
    except Exception as e:
        logger.error("tool.improve_character_profile.fail", error=str(e), exc_info=True)
        return CharacterProfile(
            name=existing_profile.get("name") or "",
            short_description=existing_profile.get("short_description") or "",
            profile_text=existing_profile.get("profile_text") or "",
            image_url=existing_profile.get("image_url"),
        )


@tool
async def generate_baseline_questions(
    category: str,
    character_profiles: List[Dict],
    synopsis: Dict,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
    analysis: Optional[Dict[str, Any]] = None,
    num_questions: Optional[int] = None,
) -> List[QuizQuestion]:
    """Generate N baseline questions in one structured call (zero-knowledge)."""
    n = int(num_questions) if isinstance(num_questions, int) and num_questions > 0 else getattr(
        getattr(settings, "quiz", object()), "baseline_questions_n", 5
    )
    m = getattr(getattr(settings, "quiz", object()), "max_options_m", 4)

    analysis = _resolve_analysis(category, synopsis, analysis)
    prompt = prompt_manager.get_prompt("question_generator")
    messages = prompt.invoke(
        {
            "category": analysis["normalized_category"],
            "outcome_kind": analysis["outcome_kind"],
            "creativity_mode": analysis["creativity_mode"],
            "intent": analysis.get("intent", "identify"),
            "character_profiles": character_profiles,
            "synopsis": synopsis,
            "count": n,
            "max_options": m,
            # back-compat; harmless surplus variable for templates that accept it
            "normalized_category": analysis["normalized_category"],
        }
    ).messages

    try:
        qlist: QuestionList = await invoke_structured(
            tool_name="question_generator",
            messages=messages,
            response_model=QuestionList,
            trace_id=trace_id,
            session_id=session_id,
        )
        questions_raw = list(getattr(qlist, "questions", []))[: max(n, 0)]
    except Exception as e:
        logger.warning("tool.generate_baseline_questions.list_fallback", error=str(e))
        # Fallback: ask for a bare list but *typed*; the helper validates via a TypeAdapter.
        adapter = TypeAdapter(List[QuestionOut])
        questions_raw: List[QuestionOut] = await invoke_structured(
            tool_name="question_generator",
            messages=messages,
            response_model=adapter,
            trace_id=trace_id,
            session_id=session_id,
        )
        questions_raw = list(questions_raw or [])[: max(n, 0)]

    out: List[QuizQuestion] = []
    for q in questions_raw:
        opts = _normalize_options(getattr(q, "options", []), max_options=m)
        opts = _ensure_min_options(opts, minimum=2)
        qt = (getattr(q, "question_text", "") or "").strip() or "Baseline question"
        out.append(QuizQuestion(question_text=qt, options=opts))
    logger.info("tool.generate_baseline_questions.ok", count=len(out))
    return out


@tool
async def generate_next_question(
    quiz_history: List[Dict],
    character_profiles: List[Dict],
    synopsis: Dict,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
    analysis: Optional[Dict[str, Any]] = None,
) -> QuizQuestion:
    """Generate one adaptive next question based on prior answers (zero-knowledge)."""
    logger.info(
        "tool.generate_next_question.start",
        history_len=len(quiz_history or []),
        character_count=len(character_profiles or []),
    )

    # Derive best-effort category from synopsis.title ("Quiz: X")
    derived_category = ""
    try:
        if isinstance(synopsis, dict):
            title = synopsis.get("title", "")
            if isinstance(title, str) and title.startswith("Quiz:"):
                derived_category = title.split("Quiz:", 1)[1].strip()
    except Exception:
        derived_category = ""

    m = getattr(getattr(settings, "quiz", object()), "max_options_m", 4)
    analysis = _resolve_analysis(derived_category or "", synopsis, analysis)

    prompt = prompt_manager.get_prompt("next_question_generator")
    messages = prompt.invoke(
        {
            "quiz_history": quiz_history,
            "character_profiles": character_profiles,
            "synopsis": synopsis,
            "max_options": m,
            "category": analysis["normalized_category"],
            "outcome_kind": analysis["outcome_kind"],
            "creativity_mode": analysis["creativity_mode"],
            "intent": analysis.get("intent", "identify"),
            "normalized_category": analysis["normalized_category"],  # back-compat
        }
    ).messages

    try:
        q_out: QuestionOut = await invoke_structured(
            tool_name="next_question_generator",
            messages=messages,
            response_model=QuestionOut,
            trace_id=trace_id,
            session_id=session_id,
        )
        opts = [
            {"text": o.text, **({"image_url": o.image_url} if getattr(o, "image_url", None) else {})}
            for o in getattr(q_out, "options", [])
        ]
        opts = _normalize_options(opts, max_options=m)
        opts = _ensure_min_options(opts, minimum=2)
        qt = (getattr(q_out, "question_text", "") or "").strip() or "Next question"
        logger.info("tool.generate_next_question.ok")
        return QuizQuestion(question_text=qt, options=opts)
    except Exception as e:
        logger.error("tool.generate_next_question.fail", error=str(e), exc_info=True)
        return QuizQuestion(
            question_text="(Unable to generate the next question right now)",
            options=[{"text": "Continue"}, {"text": "Skip"}],
        )


@tool
async def decide_next_step(
    quiz_history: List[Dict],
    character_profiles: List[Dict],
    synopsis: Dict,
    analysis: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> NextStepDecision:
    """Decide whether to ask one more question or finish now (no disambiguation flow)."""
    def _to_dict(x):
        if hasattr(x, "model_dump"):
            return x.model_dump()
        if hasattr(x, "dict"):
            return x.dict()
        return x

    inferred_category = ""
    try:
        if isinstance(synopsis, dict):
            title = synopsis.get("title", "")
            if isinstance(title, str) and title.startswith("Quiz:"):
                inferred_category = title.split("Quiz:", 1)[1].strip()
    except Exception:
        inferred_category = ""

    analysis = _resolve_analysis(inferred_category or "General", synopsis, analysis)
    prompt = prompt_manager.get_prompt("decision_maker")
    messages = prompt.invoke(
        {
            "quiz_history": [_to_dict(i) for i in (quiz_history or [])],
            "character_profiles": [_to_dict(c) for c in (character_profiles or [])],
            "synopsis": _to_dict(synopsis) if synopsis is not None else {},
            "min_questions_before_finish": getattr(getattr(settings, "quiz", object()), "min_questions_before_early_finish", 6),
            "confidence_threshold": getattr(getattr(settings, "quiz", object()), "early_finish_confidence", 0.9),
            "max_total_questions": getattr(getattr(settings, "quiz", object()), "max_total_questions", 20),
            "category": analysis["normalized_category"],
            "outcome_kind": analysis["outcome_kind"],
            "creativity_mode": analysis["creativity_mode"],
            "intent": analysis.get("intent", "identify"),
        }
    ).messages

    return await invoke_structured(
        tool_name="decision_maker",
        messages=messages,
        response_model=NextStepDecision,
        trace_id=trace_id,
        session_id=session_id,
    )


@tool
async def write_final_user_profile(
    winning_character: Dict,
    quiz_history: List[Dict],
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> FinalResult:
    """Write the final, personalized user profile result (zero-knowledge)."""
    logger.info("tool.write_final_user_profile.start", character=winning_character.get("name"))
    prompt = prompt_manager.get_prompt("final_profile_writer")
    messages = prompt.invoke(
        {
            "winning_character_name": winning_character.get("name"),
            "quiz_history": quiz_history,
            # Optional steering; prompts accept these if present
            "category": winning_character.get("category") or "",
            "creativity_mode": "balanced",
            "outcome_kind": "types",
            "intent": winning_character.get("intent") or "identify",
        }
    ).messages
    try:
        out: FinalResult = await invoke_structured(
            tool_name="final_profile_writer",
            messages=messages,
            response_model=FinalResult,
            trace_id=trace_id,
            session_id=session_id,
        )
        logger.info("tool.write_final_user_profile.ok")
        return out
    except Exception as e:
        logger.error("tool.write_final_user_profile.fail", error=str(e), exc_info=True)
        return FinalResult(
            title="We couldn't determine your result",
            description="Please try again with a different topic.",
            image_url=None,
        )
