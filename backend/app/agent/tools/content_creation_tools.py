# backend/app/agent/tools/content_creation_tools.py
"""
Agent Tools: Content Creation

These tools create the content used by the quiz:
- category synopsis (title + summary)
- character profiles
- baseline and adaptive questions
- final result

They are thin adapters over prompts + LLMService to keep behavior testable and
allow central configuration from settings (Azure/YAML/defaults).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Iterable, Union

import asyncio
import structlog
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from pydantic import ValidationError

from app.agent.prompts import prompt_manager
from app.agent.state import CharacterProfile, QuizQuestion, Synopsis
from app.agent.schemas import QuestionOut, QuestionList, NextStepDecision, QuestionAnswer  # strict, shared schema for LLM output
from app.models.api import FinalResult  # authoritative final result type
from app.services.llm_service import llm_service
from app.core.config import settings

logger = structlog.get_logger(__name__)


# -------------------------
# Helper normalization
# -------------------------

def _iter_texts(raw: Iterable[Any]) -> Iterable[str]:
    """
    Yield normalized option texts from possibly mixed inputs:
      - strings
      - dicts with 'text' or 'label'
      - pydantic/objects with a 'text' attribute
      - anything else convertible to string
    Empty/whitespace-only strings are skipped.
    """
    for opt in raw or []:
        text: Optional[str] = None
        if isinstance(opt, str):
            text = opt
        elif isinstance(opt, dict):
            text = opt.get("text") or opt.get("label")
        elif hasattr(opt, "text"):
            try:
                text = getattr(opt, "text")
            except Exception:
                text = None
        else:
            text = str(opt)

        if text is None:
            continue
        t = str(text).strip()
        if t:
            yield t


def _dedupe_case_insensitive(items: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for t in items:
        key = t.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def _normalize_options(raw: List[Any], max_options: Optional[int] = None) -> List[Dict[str, str]]:
    """
    Normalize LLM-generated options into [{'text': '...'}] form expected by state/UI.
    - trims
    - dedupes case-insensitively
    - applies max_options cap if provided
    """
    texts = _dedupe_case_insensitive(_iter_texts(raw))
    if max_options is not None and max_options > 0:
        texts = texts[: max_options]
    return [{"text": t} for t in texts]


def _ensure_min_options(options: List[Dict[str, str]], minimum: int = 2) -> List[Dict[str, str]]:
    """
    Ensure at least `minimum` options exist. If not, pad with generic choices.
    """
    if len(options) >= minimum:
        return options
    pad = minimum - len(options)
    # deterministic fillers
    fillers = [{"text": "Yes"}, {"text": "No"}, {"text": "Maybe"}, {"text": "Skip"}]
    return options + fillers[:pad]


# -------------------------
# Tools
# -------------------------

@tool
async def generate_category_synopsis(
    category: str,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Synopsis:
    """
    Generates a rich, engaging synopsis for the given quiz category.
    """
    logger.info("tool.generate_category_synopsis.start", category=category)
    prompt = prompt_manager.get_prompt("synopsis_generator")
    messages = prompt.invoke({"category": category}).messages
    try:
        out = await llm_service.get_structured_response(
            tool_name="synopsis_generator",
            messages=messages,
            response_model=Synopsis,
            trace_id=trace_id,
            session_id=session_id,
        )
        logger.info("tool.generate_category_synopsis.ok", title=out.title)
        return out
    except Exception as e:
        logger.error("tool.generate_category_synopsis.fail", error=str(e), exc_info=True)
        # Minimal safe fallback to keep UX flowing
        return Synopsis(title=f"Quiz: {category}", summary="")


@tool
async def draft_character_profile(
    character_name: str,
    category: str,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> CharacterProfile:
    """
    Drafts a new character profile for a given archetype (character_name).
    """
    logger.info("tool.draft_character_profile.start", character_name=character_name, category=category)
    prompt = prompt_manager.get_prompt("profile_writer")
    messages = prompt.invoke({"character_name": character_name, "category": category}).messages

    try:
        out = await llm_service.get_structured_response(
            tool_name="profile_writer",
            messages=messages,
            response_model=CharacterProfile,
            trace_id=trace_id,
            session_id=session_id,
        )
        logger.debug("tool.draft_character_profile.ok", character=out.name)
        return out
    except ValidationError as e:
        logger.error("tool.draft_character_profile.validation", error=str(e), exc_info=True)
        # fallback minimal profile
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
    """
    Improves an existing character profile using feedback text.
    """
    logger.info("tool.improve_character_profile.start", name=existing_profile.get("name"))
    prompt = prompt_manager.get_prompt("profile_improver")
    messages = prompt.invoke({"existing_profile": existing_profile, "feedback": feedback}).messages
    try:
        out = await llm_service.get_structured_response(
            "profile_improver", messages, CharacterProfile, trace_id, session_id
        )
        logger.debug("tool.improve_character_profile.ok", name=out.name)
        return out
    except Exception as e:
        logger.error("tool.improve_character_profile.fail", error=str(e), exc_info=True)
        # Return original (wrapped) if improvement fails
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
) -> List[QuizQuestion]:
    """
    Generates all baseline questions in a **single** structured call.
    Enforces N (count) and M (options cap) from settings.quiz.
    """
    n = getattr(settings.quiz, "baseline_questions_n", 5)
    m = getattr(settings.quiz, "max_options_m", 4)
    prompt = prompt_manager.get_prompt("question_generator")
    messages = prompt.invoke({
        "category": category,
        "character_profiles": character_profiles,
        "synopsis": synopsis,
        "count": n,
        "max_options": m,
    }).messages
    qlist = await llm_service.get_structured_response(
        tool_name="question_generator",
        messages=messages,
        response_model=QuestionList,
        trace_id=trace_id,
        session_id=session_id,
    )
    out: List[QuizQuestion] = []
    for q in getattr(qlist, "questions", []):
        opts = _normalize_options(getattr(q, "options", []), max_options=m)
        opts = _ensure_min_options(opts, minimum=2)
        qt = (getattr(q, "question_text", "") or "").strip() or "Baseline question"
        out.append(QuizQuestion(question_text=qt, options=opts))
    return out


@tool
async def generate_next_question(
    quiz_history: List[Dict],
    character_profiles: List[Dict],
    synopsis: Dict,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> QuizQuestion:
    """
    Generates a single, new adaptive question based on the user's previous answers.
    """
    logger.info(
        "tool.generate_next_question.start",
        history_len=len(quiz_history or []),
        character_count=len(character_profiles or []),
    )
    prompt = prompt_manager.get_prompt("next_question_generator")
    m = getattr(settings.quiz, "max_options_m", 4)
    messages = prompt.invoke({
        "quiz_history": quiz_history,
        "character_profiles": character_profiles,
        "synopsis": synopsis,
        "max_options": m,
    }).messages

    try:
        q_out = await llm_service.get_structured_response(
            tool_name="next_question_generator",
            messages=messages,
            response_model=QuestionOut,
            trace_id=trace_id,
            session_id=session_id,
        )
        opts = [{"text": o.text, **({"image_url": o.image_url} if getattr(o, "image_url", None) else {})}
                for o in getattr(q_out, "options", [])]
        opts = _normalize_options(opts, max_options=m)
        opts = _ensure_min_options(opts, minimum=2)
        qt = (getattr(q_out, "question_text", "") or "").strip() or "Next question"
        return QuizQuestion(question_text=qt, options=opts)
    except Exception as e:
        logger.error("tool.generate_next_question.fail", error=str(e), exc_info=True)
        # fallback safe dummy to keep the flow moving (caller may choose to stop)
        return QuizQuestion(
            question_text="(Unable to generate the next question right now)",
            options=[{"text": "Continue"}, {"text": "Skip"}],
        )


@tool
async def decide_next_step(
    quiz_history: List[Dict],
    character_profiles: List[Dict],
    synopsis: Dict,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> NextStepDecision:
    """
    Decide whether to ask one more question or finish now.
    """
    prompt = prompt_manager.get_prompt("decision_maker")
    messages = prompt.invoke({
        "quiz_history": quiz_history,
        "character_profiles": character_profiles,
        "synopsis": synopsis,
        "min_questions_before_finish": getattr(settings.quiz, "min_questions_before_early_finish", 6),
        "confidence_threshold": getattr(settings.quiz, "early_finish_confidence", 0.9),
        "max_total_questions": getattr(settings.quiz, "max_total_questions", 20),
    }).messages
    return await llm_service.get_structured_response(
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
    """
    Writes the final, personalized user profile (the "You are..." result).
    """
    logger.info("tool.write_final_user_profile.start", character=winning_character.get("name"))
    prompt = prompt_manager.get_prompt("final_profile_writer")
    messages = prompt.invoke({
        "winning_character_name": winning_character.get("name"),
        "quiz_history": quiz_history,
    }).messages
    try:
        out = await llm_service.get_structured_response(
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
        # Minimal fallback using optional image_url
        return FinalResult(
            title="We couldn't determine your result",
            description="Please try again with a different topic.",
            image_url=None,
        )
