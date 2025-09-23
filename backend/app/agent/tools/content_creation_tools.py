"""
Agent Tools: Content Creation

These tools create the content used by the quiz:
- category synopsis (title + summary)
- character profiles (canonical when media)
- baseline and adaptive questions
- final result

Key updates in this rewrite:
- Strong, general-purpose topic analysis so *any* topic works.
- When the topic is a media title, character profiles must be CANONICAL (no invention).
- The question prompts are fed with `normalized_category`, `outcome_kind`, and
  `creativity_mode` so updated prompt templates do not error and can adapt tone.
- We keep tool names/signatures unchanged for compatibility with the graph/registry.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Iterable, Union, Literal

import asyncio
import structlog
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from pydantic import ValidationError

from app.agent.prompts import prompt_manager
from app.agent.state import CharacterProfile, QuizQuestion, Synopsis
from app.agent.schemas import QuestionOut, QuestionList, NextStepDecision  # strict output models
from app.models.api import FinalResult
from app.services.llm_service import llm_service
from app.core.config import settings

logger = structlog.get_logger(__name__)


# -------------------------
# Topic analysis (robust + local)
# -------------------------

_MEDIA_HINT_WORDS = {
    "season","episode","saga","trilogy","universe","series","show","sitcom","drama",
    "film","movie","novel","book","manga","anime","cartoon","comic","graphic novel",
    "musical","play","opera","broadway","videogame","video game","game","franchise"
}
_SERIOUS_HINTS = {
    "disc","myers","mbti","enneagram","big five","ocean","hexaco","strengthsfinder",
    "attachment style","aptitude","assessment","clinical","medical","doctor","physician",
    "lawyer","attorney","engineer","accountant","scientist","resume","cv","career","diagnostic"
}
_TYPE_SYNONYMS = {"type","types","kind","kinds","style","styles","variety","varieties","flavor","flavors","breed","breeds"}

def _looks_like_media_title(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    lc = t.casefold()
    if any(w in lc for w in _MEDIA_HINT_WORDS):
        return True
    if " " in t and t[:1].isupper():
        if not any(k in lc for k in _TYPE_SYNONYMS):
            return True
    return False

def _simple_singularize(noun: str) -> str:
    s = (noun or "").strip()
    if not s:
        return s
    lower = s.lower()
    if lower.endswith("ies") and len(s) > 3:
        return s[:-3] + "y"
    if lower.endswith("ses") and len(s) > 3:
        return s[:-2]
    if lower.endswith("s") and not lower.endswith("ss"):
        return s[:-1]
    return s

def _analyze_topic(category: str, synopsis: Optional[Dict] = None) -> Dict[str, Any]:
    """
    Decide how to steer prompts for arbitrary topics.
    Returns:
      - normalized_category
      - outcome_kind: 'characters' | 'types' | 'archetypes' | 'profiles'
      - creativity_mode: 'playful' | 'balanced' | 'grounded'
      - is_media: bool
    """
    raw = (category or "").strip()
    lc = raw.casefold()
    syn_sum = ""
    try:
        if isinstance(synopsis, dict):
            syn_sum = (synopsis.get("summary") or synopsis.get("synopsis") or synopsis.get("synopsis_text") or "").strip()
    except Exception:
        syn_sum = ""

    def has(tokens: set[str]) -> bool:
        return any(t in lc for t in tokens) or any(t in syn_sum.casefold() for t in tokens)

    is_media = _looks_like_media_title(raw) or raw.endswith(" Characters") or raw.endswith(" characters")
    is_serious = has(_SERIOUS_HINTS)

    if is_serious:
        return {
            "normalized_category": raw or "General",
            "outcome_kind": "profiles",
            "creativity_mode": "grounded",
            "is_media": False,
        }

    if is_media:
        base = raw.removesuffix(" Characters").removesuffix(" characters").strip()
        return {
            "normalized_category": f"{base} Characters",
            "outcome_kind": "characters",
            "creativity_mode": "balanced",
            "is_media": True,
        }

    tokens = raw.split()
    if len(tokens) <= 2 and raw.isalpha():
        singular = _simple_singularize(raw)
        return {
            "normalized_category": f"Type of {singular}",
            "outcome_kind": "types",
            "creativity_mode": "playful",
            "is_media": False,
        }

    if any(k in lc for k in _TYPE_SYNONYMS):
        return {
            "normalized_category": raw,
            "outcome_kind": "types",
            "creativity_mode": "balanced",
            "is_media": False,
        }

    return {
        "normalized_category": raw or "General",
        "outcome_kind": "archetypes",
        "creativity_mode": "balanced",
        "is_media": False,
    }


# -------------------------
# Helper normalization for options
# -------------------------

def _iter_texts(raw: Iterable[Any]) -> Iterable[str]:
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
    texts = _dedupe_case_insensitive(_iter_texts(raw))
    if max_options is not None and max_options > 0:
        texts = texts[: max_options]
    return [{"text": t} for t in texts]

def _ensure_min_options(options: List[Dict[str, str]], minimum: int = 2) -> List[Dict[str, str]]:
    if len(options) >= minimum:
        return options
    pad = minimum - len(options)
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
    We pass steering vars so updated prompts can adapt tone/rigor.
    """
    logger.info("tool.generate_category_synopsis.start", category=category)
    analysis = _analyze_topic(category)
    prompt = prompt_manager.get_prompt("synopsis_generator")
    messages = prompt.invoke({
        "category": category,
        "normalized_category": analysis["normalized_category"],
        "outcome_kind": analysis["outcome_kind"],
        "creativity_mode": analysis["creativity_mode"],
    }).messages
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
        return Synopsis(title=f"Quiz: {category}", summary="")


@tool
async def draft_character_profile(
    character_name: str,
    category: str,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> CharacterProfile:
    """
    Drafts a character profile. If the category is a media title, produce
    CANONICAL descriptions for that character (no invented facts).
    Otherwise, creative/archetypal descriptions are fine.
    """
    logger.info("tool.draft_character_profile.start", character_name=character_name, category=category)
    analysis = _analyze_topic(category)
    is_media = analysis["is_media"]

    if is_media and analysis["outcome_kind"] == "characters":
        # Build a canonical-focused instruction while keeping tool_name/stucture.
        system = (
            "You are a concise encyclopedic writer. Produce accurate, neutral, and helpful profiles "
            "based strictly on widely-known canonical information from the referenced work. Do not invent facts."
        )
        user = (
            f"Work: {analysis['normalized_category'].removesuffix(' Characters')}\n"
            f"Character: {character_name}\n\n"
            "Return JSON with:\n"
            "- name (string)\n"
            "- short_description (1 sentence; what defines them)\n"
            "- profile_text (2 short paragraphs; key traits, motivations, notable relationships/moments)."
        )
        try:
            out = await llm_service.get_structured_response(
                tool_name="profile_writer",
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_model=CharacterProfile,
                trace_id=trace_id,
                session_id=session_id,
            )
            logger.debug("tool.draft_character_profile.ok.canonical", character=out.name)
            return out
        except Exception as e:
            logger.warning("tool.draft_character_profile.canonical_fail_fallback", error=str(e), exc_info=True)
            # Fall through to generic prompt as a safe fallback

    # Non-media or fallback: creative/archetypal (original behavior with added hints)
    prompt = prompt_manager.get_prompt("profile_writer")
    messages = prompt.invoke({
        "character_name": character_name,
        "category": analysis["normalized_category"],
        "outcome_kind": analysis["outcome_kind"],
        "creativity_mode": analysis["creativity_mode"],
    }).messages
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
    If the profile looks media-canonical, keep fact-checking tone.
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
    Generates all baseline questions in a single structured call.
    Ensures updated prompt vars are supplied and options normalized.
    """
    n = getattr(settings.quiz, "baseline_questions_n", 5)
    m = getattr(settings.quiz, "max_options_m", 4)

    analysis = _analyze_topic(category, synopsis)
    prompt = prompt_manager.get_prompt("question_generator")
    messages = prompt.invoke({
        "category": category,
        "normalized_category": analysis["normalized_category"],
        "outcome_kind": analysis["outcome_kind"],
        "creativity_mode": analysis["creativity_mode"],
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
    logger.info("tool.generate_baseline_questions.ok", count=len(out))
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
    Supplies steering vars so the prompt can focus on differentiating the right outcomes.
    """
    logger.info(
        "tool.generate_next_question.start",
        history_len=len(quiz_history or []),
        character_count=len(character_profiles or []),
    )

    # Derive category best-effort from synopsis title ("Quiz: X") if not known here.
    derived_category = ""
    try:
        title = (synopsis or {}).get("title") if isinstance(synopsis, dict) else None
        if isinstance(title, str) and title.startswith("Quiz:"):
            derived_category = title.split("Quiz:", 1)[1].strip()
    except Exception:
        derived_category = ""

    m = getattr(settings.quiz, "max_options_m", 4)
    analysis = _analyze_topic(derived_category or "", synopsis)

    prompt = prompt_manager.get_prompt("next_question_generator")
    messages = prompt.invoke({
        "quiz_history": quiz_history,
        "character_profiles": character_profiles,
        "synopsis": synopsis,
        "max_options": m,
        "normalized_category": analysis["normalized_category"],
        "outcome_kind": analysis["outcome_kind"],
        "creativity_mode": analysis["creativity_mode"],
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
        return FinalResult(
            title="We couldn't determine your result",
            description="Please try again with a different topic.",
            image_url=None,
        )
