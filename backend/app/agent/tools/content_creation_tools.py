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

from typing import Any, Dict, List, Optional

import structlog
from langchain_core.tools import tool
from pydantic import BaseModel, ValidationError

from app.agent.prompts import prompt_manager
from app.agent.state import CharacterProfile, QuizQuestion, Synopsis
from app.models.api import FinalResult  # authoritative final result type
from app.services.llm_service import llm_service
from app.core.config import settings

logger = structlog.get_logger(__name__)

# -------------------------
# Helper normalization
# -------------------------

def _normalize_options(raw: List[Any]) -> List[Dict[str, str]]:
    """
    Normalize LLM-generated options into [{'text': '...'}] form expected by state/UI.
    Accept both strings and dicts with a 'text' (or 'label') field.
    """
    out: List[Dict[str, str]] = []
    for opt in raw or []:
        if isinstance(opt, str):
            t = opt.strip()
            if t:
                out.append({"text": t})
        elif isinstance(opt, dict):
            txt = str(opt.get("text") or opt.get("label") or "").strip()
            if txt:
                out.append({"text": txt})
        else:
            s = str(opt).strip()
            if s:
                out.append({"text": s})
    return out

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
        # Minimal safe fallback to keep UX flowing; empty strings are acceptable to state.
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
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> List[QuizQuestion]:
    """
    Generates the initial set of baseline questions for the quiz.
    Enforces N (count) and M (options cap) from settings.quiz.
    """
    logger.info(
        "tool.generate_baseline_questions.start",
        category=category,
        character_count=len(character_profiles or []),
        n=settings.quiz.baseline_questions_n,
        m=settings.quiz.max_options_m,
    )
    prompt = prompt_manager.get_prompt("question_generator")
    messages = prompt.invoke({
        "category": category,
        "character_profiles": character_profiles,
    }).messages

    class _QOut(BaseModel):
        id: Optional[str] = None
        question_text: str
        options: List[Any]

    class _QList(BaseModel):
        questions: List[_QOut]

    try:
        resp = await llm_service.get_structured_response(
            tool_name="question_generator",
            messages=messages,
            response_model=_QList,
            trace_id=trace_id,
            session_id=session_id,
        )
        # Normalize and enforce caps
        n = settings.quiz.baseline_questions_n
        m = settings.quiz.max_options_m

        out: List[QuizQuestion] = []
        for idx, q in enumerate(resp.questions[: n]):
            opts = _normalize_options(q.options)[:m]
            if not opts:
                opts = [{"text": "Yes"}, {"text": "No"}]
            out.append(QuizQuestion(question_text=q.question_text, options=opts))

        logger.info("tool.generate_baseline_questions.ok", produced=len(out))
        return out
    except Exception as e:
        logger.error("tool.generate_baseline_questions.fail", error=str(e), exc_info=True)
        return []


@tool
async def generate_next_question(
    quiz_history: List[Dict],
    character_profiles: List[Dict],
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> QuizQuestion:
    """
    Generates a single, new adaptive question based on the user's previous answers.
    """
    logger.info("tool.generate_next_question.start", history_len=len(quiz_history or []), character_count=len(character_profiles or []))
    prompt = prompt_manager.get_prompt("next_question_generator")
    messages = prompt.invoke({
        "quiz_history": quiz_history,
        "character_profiles": character_profiles,
    }).messages

    try:
        out = await llm_service.get_structured_response(
            tool_name="next_question_generator",
            messages=messages,
            response_model=QuizQuestion,
            trace_id=trace_id,
            session_id=session_id,
        )
        # Ensure options normalized (some prompts might return strings-only)
        out.options = _normalize_options(out.options)  # type: ignore[assignment]
        logger.debug("tool.generate_next_question.ok")
        return out
    except Exception as e:
        logger.error("tool.generate_next_question.fail", error=str(e), exc_info=True)
        # fallback safe dummy to keep the flow moving (caller may choose to stop)
        return QuizQuestion(question_text="(Unable to generate next question)", options=[{"text": "Continue"}])


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
        # Minimal fallback: empty FinalResult-like structure, but we stick to single source of truth (FinalResult model)
        return FinalResult(title="We couldn't determine your result", description="Please try again with a different topic.", image_url=None)
