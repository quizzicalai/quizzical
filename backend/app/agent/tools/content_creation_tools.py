# backend/app/agent/tools/content_creation_tools.py
"""
Agent Tools: Content Creation

These tools create the content used by the quiz:
- category synopsis (title + summary)
- character profiles (canonical when media, with lightweight RAG grounding)
- baseline and adaptive questions
- final result

Alignment notes:
- Prompts now use {category} as the canonical placeholder.
- Optional retrieval is added where facts matter:
  • Per-character Wikipedia/Web snippets -> {character_context} for bios
- We pass normalized {category} plus outcome/tone flags (harmless if a prompt ignores them).
- Character list and questions preserve option image_url when present.
- Tool names/signatures remain unchanged to match tools/__init__.py, graph.py.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Iterable

import asyncio
import structlog
from langchain_core.tools import tool
from pydantic import ValidationError

from app.agent.prompts import prompt_manager
from app.agent.state import CharacterProfile, QuizQuestion, Synopsis
from app.agent.schemas import QuestionOut, QuestionList, NextStepDecision
from app.models.api import FinalResult
from app.services.llm_service import llm_service
from app.core.config import settings

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Topic analysis (local; mirrors planning_tools heuristics)
# ---------------------------------------------------------------------------

_MEDIA_HINT_WORDS = {
    "season", "episode", "saga", "trilogy", "universe", "series", "show", "sitcom", "drama",
    "film", "movie", "novel", "book", "manga", "anime", "cartoon", "comic", "graphic novel",
    "musical", "play", "opera", "broadway", "videogame", "video game", "game", "franchise",
}
_SERIOUS_HINTS = {
    "disc", "myers", "mbti", "enneagram", "big five", "ocean", "hexaco", "strengthsfinder",
    "attachment style", "aptitude", "assessment", "clinical", "medical", "doctor", "physician",
    "lawyer", "attorney", "engineer", "accountant", "scientist", "resume", "cv", "career", "diagnostic",
}
_TYPE_SYNONYMS = {"type", "types", "kind", "kinds", "style", "styles", "variety", "varieties", "flavor", "flavors", "breed", "breeds"}


def _looks_like_media_title(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    lc = t.casefold()
    if any(w in lc for w in _MEDIA_HINT_WORDS):
        return True
    # Title-like capitalization for multi-word phrases
    if " " in t and t[:1].isupper() and not any(k in lc for k in _TYPE_SYNONYMS):
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
    Returns:
      - normalized_category (str)
      - outcome_kind: 'characters' | 'types' | 'archetypes' | 'profiles'
      - creativity_mode: 'whimsical' | 'balanced' | 'factual'
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
            "creativity_mode": "factual",
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
            "creativity_mode": "whimsical",
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

# ---------------------------------------------------------------------------
# Retrieval helpers (lightweight, best-effort)
# ---------------------------------------------------------------------------


async def _fetch_character_context(character_name: str, normalized_category: str, trace_id: Optional[str], session_id: Optional[str]) -> str:
    """
    Try to fetch a short snippet about the specific character from Wikipedia first,
    then a general web search. Returns a (possibly empty) string, truncated.
    """
    base_title = normalized_category.removesuffix(" Characters").strip()
    try:
        from app.agent.tools.data_tools import wikipedia_search, web_search  # type: ignore
    except Exception as e:
        logger.debug("content.rag.import_failed", reason=str(e))
        return ""

    text = ""
    try:
        # Prefer precise queries
        q1 = f"{character_name} ({base_title})"
        q2 = f"{character_name} {base_title}"
        q3 = f"{base_title} characters {character_name}"
        for q in (q1, q2, q3):
            try:
                res = await wikipedia_search.ainvoke({"query": q})
                if isinstance(res, str) and res.strip():
                    text = res.strip()
                    break
            except Exception:
                continue

        if not text:
            try:
                web_q = f"Who is {character_name} from {base_title}?"
                res = await web_search.ainvoke({"query": web_q, "trace_id": trace_id, "session_id": session_id})
                if isinstance(res, str) and res.strip():
                    text = res.strip()
            except Exception:
                pass
    except Exception as e:
        logger.debug("content.rag.query_failed", reason=str(e))

    if text and len(text) > 1200:
        text = text[:1200]
    return text

# ---------------------------------------------------------------------------
# Options normalization (preserve image_url)
# ---------------------------------------------------------------------------


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


def _normalize_options(raw: List[Any], max_options: Optional[int] = None) -> List[Dict[str, Any]]:
    """Coerce to [{'text', 'image_url'?}], dedupe by text (case-insensitive), keep first with media."""
    out: List[Dict[str, Any]] = []
    for opt in (raw or []):
        if isinstance(opt, str):
            item = {"text": opt.strip()}
        elif isinstance(opt, dict):
            text = (opt.get("text") or opt.get("label") or "").strip()
            if not text:
                continue
            item = {"text": text}
            img = opt.get("image_url") or opt.get("imageUrl")
            if img:
                item["image_url"] = img
        else:
            item = {"text": str(opt).strip()}
        if not item.get("text"):
            continue
        out.append(item)

    seen = set()
    uniq: List[Dict[str, Any]] = []
    for item in out:
        key = item["text"].casefold()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(item)

    if max_options and max_options > 0:
        uniq = uniq[:max_options]
    return uniq


def _ensure_min_options(options: List[Dict[str, Any]], minimum: int = 2) -> List[Dict[str, Any]]:
    if len(options) >= minimum:
        return options
    pad = minimum - len(options)
    fillers = [{"text": "Yes"}, {"text": "No"}, {"text": "Maybe"}, {"text": "Skip"}]
    return options + fillers[:pad]

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool
async def generate_category_synopsis(
    category: str,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Synopsis:
    """Generate a synopsis (title + summary) for the quiz category."""
    logger.info("tool.generate_category_synopsis.start", category=category)
    analysis = _analyze_topic(category)
    prompt = prompt_manager.get_prompt("synopsis_generator")
    messages = prompt.invoke(
        {
            "category": analysis["normalized_category"],
            "outcome_kind": analysis["outcome_kind"],
            "creativity_mode": analysis["creativity_mode"],
            # back-compat (safe to include)
            "normalized_category": analysis["normalized_category"],
        }
    ).messages
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
        return Synopsis(title=f"Quiz: {analysis['normalized_category']}", summary="")


@tool
async def draft_character_profile(
    character_name: str,
    category: str,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> CharacterProfile:
    """
    Draft a character/profile. If category is media, write CANONICAL (no invention).
    Otherwise, archetypal/creative is fine (within tone).

    NEW:
    - For media/factual topics, we fetch per-character context (Wikipedia/Web)
      and pass it to the prompt as {character_context} to ground facts.
    """
    logger.info("tool.draft_character_profile.start", character_name=character_name, category=category)
    analysis = _analyze_topic(category)
    is_media = analysis["is_media"]

    # --- Lightweight RAG: fetch per-character context when facts matter ---
    character_context = ""
    try:
        if is_media or analysis["creativity_mode"] == "factual" or analysis["outcome_kind"] in {"profiles", "characters"}:
            character_context = await _fetch_character_context(
                character_name=character_name,
                normalized_category=analysis["normalized_category"],
                trace_id=trace_id,
                session_id=session_id,
            )
    except Exception as e:
        logger.debug("tool.draft_character_profile.rag_skip", reason=str(e))
        character_context = ""

    # Canonical writer branch kept (minimal change), but now includes RAG "FACTS"
    if is_media and analysis["outcome_kind"] == "characters":
        system = (
            "You are a concise encyclopedic writer. Produce accurate, neutral, helpful profiles "
            "based strictly on widely-known canonical information from the referenced work. Do not invent facts."
        )
        facts = f"\n\nFACTS (do not contradict):\n{character_context}" if character_context else ""
        user = (
            f"Work: {analysis['normalized_category'].removesuffix(' Characters')}\n"
            f"Character: {character_name}\n"
            f"{facts}\n\n"
            "Return JSON with:\n"
            '- "name": string\n'
            '- "short_description": 1 sentence\n'
            '- "profile_text": 2 short paragraphs\n'
            '- "image_url": string | null (optional)\n'
        )
        try:
            out = await llm_service.get_structured_response(
                tool_name="profile_writer",
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_model=CharacterProfile,
                trace_id=trace_id,
                session_id=session_id,
            )
            # Name lock (light touch; graph enforces too)
            if not getattr(out, "name", None):
                out.name = character_name
            logger.debug("tool.draft_character_profile.ok.canonical", character=out.name)
            return out
        except Exception as e:
            logger.warning("tool.draft_character_profile.canonical_fallback", error=str(e), exc_info=True)
            # fall through to generic prompt

    # Generic writer path uses the standardized prompt and passes character_context (may be empty)
    prompt = prompt_manager.get_prompt("profile_writer")
    messages = prompt.invoke(
        {
            "character_name": character_name,
            "category": analysis["normalized_category"],
            "outcome_kind": analysis["outcome_kind"],
            "creativity_mode": analysis["creativity_mode"],
            "character_context": character_context,
            "normalized_category": analysis["normalized_category"],  # back-compat
        }
    ).messages
    try:
        out = await llm_service.get_structured_response(
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
    """Generate N baseline questions in one structured call."""
    n = getattr(settings.quiz, "baseline_questions_n", 5)
    m = getattr(settings.quiz, "max_options_m", 4)

    analysis = _analyze_topic(category, synopsis)
    prompt = prompt_manager.get_prompt("question_generator")
    messages = prompt.invoke(
        {
            "category": analysis["normalized_category"],
            "outcome_kind": analysis["outcome_kind"],
            "creativity_mode": analysis["creativity_mode"],
            "character_profiles": character_profiles,
            "synopsis": synopsis,
            "count": n,
            "max_options": m,
            "normalized_category": analysis["normalized_category"],  # back-compat
        }
    ).messages

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
    """Generate one adaptive next question based on prior answers."""
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

    m = getattr(settings.quiz, "max_options_m", 4)
    analysis = _analyze_topic(derived_category or "", synopsis)

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
            "normalized_category": analysis["normalized_category"],  # back-compat
        }
    ).messages

    try:
        q_out = await llm_service.get_structured_response(
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
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> NextStepDecision:
    """Decide whether to ask one more question or finish now."""
    # Defensive coercion: ensure plain dicts are sent into the prompt/LLM
    def _to_dict(x):
        if hasattr(x, "model_dump"):
            return x.model_dump()
        if hasattr(x, "dict"):
            return x.dict()
        return x

    # Try to infer a category string for the prompt (safe if ignored)
    inferred_category = ""
    try:
        if isinstance(synopsis, dict):
            title = synopsis.get("title", "")
            if isinstance(title, str) and title.startswith("Quiz:"):
                inferred_category = title.split("Quiz:", 1)[1].strip()
    except Exception:
        inferred_category = ""

    prompt = prompt_manager.get_prompt("decision_maker")
    messages = prompt.invoke(
        {
            "quiz_history": [_to_dict(i) for i in (quiz_history or [])],
            "character_profiles": [_to_dict(c) for c in (character_profiles or [])],
            "synopsis": _to_dict(synopsis) if synopsis is not None else {},
            "min_questions_before_finish": getattr(settings.quiz, "min_questions_before_early_finish", 6),
            "confidence_threshold": getattr(settings.quiz, "early_finish_confidence", 0.9),
            "max_total_questions": getattr(settings.quiz, "max_total_questions", 20),
            "category": inferred_category or "General",  # aligns with prompt; harmless if unused
            "outcome_kind": "types",                     # safe default
            "creativity_mode": "balanced",               # safe default
        }
    ).messages

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
    """Write the final, personalized user profile result."""
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
        }
    ).messages
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
