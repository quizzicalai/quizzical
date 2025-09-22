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
from app.agent.schemas import QuestionOut  # strict, shared schema for LLM output
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
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> List[QuizQuestion]:
    """
    Generates the initial set of baseline questions for the quiz.
    Enforces N (count) and M (options cap) from settings.quiz.

    CHANGE (surgical):
    - Generate N single-question structured calls in PARALLEL (bounded).
    - Use shared strict schema QuestionOut for LLM responses.
    - Honor settings.llm_runtime.per_call_timeout_s and settings.quiz.question_concurrency.
    - Normalize options to the UI/state shape and guarantee at least two options.
    """
    n = getattr(settings.quiz, "baseline_questions_n", 5)
    m = getattr(settings.quiz, "max_options_m", 4)
    per_call_timeout = getattr(getattr(settings, "llm_runtime", object()), "per_call_timeout_s", 30)
    concurrency = getattr(getattr(settings, "quiz", object()), "question_concurrency", None) or min(8, max(1, n))

    logger.info(
        "tool.generate_baseline_questions.start",
        category=category,
        character_count=len(character_profiles or []),
        n=n,
        m=m,
        concurrency=concurrency,
        timeout_s=per_call_timeout,
    )

    # Base prompt/messages for baseline questions
    prompt = prompt_manager.get_prompt("question_generator")
    base_messages = prompt.invoke({
        "category": category,
        "character_profiles": character_profiles,
    }).messages

    # We add a final instruction to ensure exactly ONE question per call.
    instruction = HumanMessage(
        content=(
            "Return exactly ONE baseline multiple-choice question as structured JSON. "
            "It must include `question_text` and an `options` array of answer choices. "
            f"Use at most {m} options."
        )
    )

    sem = asyncio.Semaphore(concurrency)
    results: List[Optional[QuizQuestion]] = [None] * n

    async def _one(idx: int) -> None:
        try:
            async with sem:
                # Copy base messages and append the instruction for single-question mode
                messages = list(base_messages) + [instruction]
                q_out = await asyncio.wait_for(
                    llm_service.get_structured_response(
                        tool_name="question_generator",
                        messages=messages,
                        response_model=QuestionOut,
                        trace_id=trace_id,
                        session_id=session_id,
                    ),
                    timeout=per_call_timeout,
                )

                opts = _normalize_options(getattr(q_out, "options", []), max_options=m)
                if m is not None and m < 2:
                    logger.warning("quiz.max_options_m < 2; padding to 2 options for FE compatibility", m=m)
                opts = _ensure_min_options(opts, minimum=2)

                qt = (getattr(q_out, "question_text", "") or "").strip()
                if not qt:
                    qt = f"Question {idx + 1}"

                results[idx] = QuizQuestion(question_text=qt, options=opts)
        except asyncio.TimeoutError:
            logger.warning("baseline_question.timeout", index=idx, timeout_s=per_call_timeout)
        except Exception as e:
            logger.error("baseline_question.fail", index=idx, error=str(e), exc_info=True)

    tasks = [asyncio.create_task(_one(i)) for i in range(n)]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    out: List[QuizQuestion] = [q for q in results if q is not None]

    logger.info("tool.generate_baseline_questions.ok", requested=n, produced=len(out))
    return out


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
    logger.info(
        "tool.generate_next_question.start",
        history_len=len(quiz_history or []),
        character_count=len(character_profiles or []),
    )
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

        # Normalize options defensively (in case prompt returns strings)
        max_m = getattr(settings.quiz, "max_options_m", None)
        out.options = _normalize_options(out.options, max_options=max_m)  # type: ignore[assignment]
        if max_m is not None and max_m < 2:
            logger.warning("quiz.max_options_m < 2; padding to 2 options for FE compatibility", m=max_m)
        out.options = _ensure_min_options(out.options, minimum=2)  # type: ignore[arg-type]

        if not getattr(out, "question_text", "").strip():
            out.question_text = "Next question"  # type: ignore[assignment]

        logger.debug("tool.generate_next_question.ok")
        return out
    except Exception as e:
        logger.error("tool.generate_next_question.fail", error=str(e), exc_info=True)
        # fallback safe dummy to keep the flow moving (caller may choose to stop)
        return QuizQuestion(
            question_text="(Unable to generate the next question right now)",
            options=[{"text": "Continue"}, {"text": "Skip"}],
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
