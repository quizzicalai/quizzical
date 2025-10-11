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
import re
import json

import asyncio
import structlog
from langchain_core.tools import tool
from pydantic import ValidationError, TypeAdapter

from app.agent.prompts import prompt_manager
from app.agent.state import CharacterProfile, QuizQuestion, Synopsis
from app.agent.schemas import QuestionOut, QuestionList, NextStepDecision
from app.models.api import FinalResult
from app.services.llm_service import llm_service
from app.core.config import settings  # (existing import; used below)

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
    "lawyer", "attorney", "engineer", "accountant", "scientist", "resume", "cv", "career",
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
# Retrieval helpers (lightweight, best-effort) + Policy (ADD-ONLY)
# ---------------------------------------------------------------------------

def _is_media_category(normalized_category: str) -> bool:
    try:
        return normalized_category.endswith(" Characters")
    except Exception:
        return False


def _policy_allows(kind: str, *, is_media: bool) -> bool:
    """
    kind: 'wiki' | 'web'
    If retrieval config is absent, allow (back-compat). Budget consumption
    is handled below: Wikipedia consumes here; web search consumes inside tool.
    """
    r = getattr(settings, "retrieval", None)
    if not r:
        return True
    policy = (getattr(r, "policy", "off") or "off").lower()
    if policy == "off":
        return False
    if kind == "wiki" and not bool(getattr(r, "allow_wikipedia", False)):
        return False
    if kind == "web" and not bool(getattr(r, "allow_web", False)):
        return False
    if policy == "media_only" and not is_media:
        return False
    return True


async def _fetch_character_context(character_name: str, normalized_category: str, trace_id: Optional[str], session_id: Optional[str]) -> str:
    """
    Try to fetch a short snippet about the specific character from Wikipedia first,
    then a general web search. Returns a (possibly empty) string, truncated.
    """
    media = _is_media_category(normalized_category)
    # Gate early if policy denies both
    if not (_policy_allows("wiki", is_media=media) or _policy_allows("web", is_media=media)):
        return ""

    base_title = normalized_category.removesuffix(" Characters").strip()
    try:
        from app.agent.tools.data_tools import wikipedia_search, web_search, consume_retrieval_slot  # type: ignore
    except Exception as e:
        logger.debug("content.rag.import_failed", reason=str(e))
        return ""

    text = ""
    try:
        # Prefer precise queries
        q1 = f"{character_name} ({base_title})"
        q2 = f"{character_name} {base_title}"
        q3 = f"{base_title} characters {character_name}"

        # Budgeted Wikipedia calls first (when allowed)
        if _policy_allows("wiki", is_media=media):
            for q in (q1, q2, q3):
                if not consume_retrieval_slot(trace_id, session_id):
                    break
                try:
                    # wikipedia_search is a sync tool; invoke via executor-safe .invoke
                    res = await asyncio.get_event_loop().run_in_executor(None, wikipedia_search.invoke, {"query": q})
                    if isinstance(res, str) and res.strip():
                        text = res.strip()
                        break
                except Exception:
                    continue

        # Fallback to web (budget enforced inside web_search)
        if not text and _policy_allows("web", is_media=media):
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
            # Avoid stringifying None into "None"
            if opt is None:
                text = None
            else:
                text = str(opt)
        if text is None:
            continue
        t = str(text).strip()
        if t:
            yield t


# Robust coercion + normalization helpers (fix for repr-in-text bug)
def _option_to_dict(opt: Any) -> Dict[str, Any]:
    """
    Coerce option (str | dict | pydantic | dataclass | object-with-text) -> {'text', 'image_url'?}.
    Avoids using str(opt) which can produce a repr like "text='A' image_url=None".
    """
    # String literal
    if isinstance(opt, str):
        return {"text": opt.strip()}

    # Already a dict
    if isinstance(opt, dict):
        text = (opt.get("text") or opt.get("label") or opt.get("option") or "").strip()
        out: Dict[str, Any] = {"text": text}
        img = opt.get("image_url") or opt.get("imageUrl") or opt.get("image")
        if img:
            out["image_url"] = img
        return out

    # Pydantic v2 model
    if hasattr(opt, "model_dump"):
        data = opt.model_dump()
        text = str(data.get("text") or data.get("label") or "").strip()
        out: Dict[str, Any] = {"text": text}
        img = data.get("image_url") or data.get("imageUrl") or data.get("image")
        if img:
            out["image_url"] = img
        return out

    # Dataclass
    if hasattr(opt, "__dataclass_fields__"):
        text = str(getattr(opt, "text", getattr(opt, "label", ""))).strip()
        out: Dict[str, Any] = {"text": text}
        img = getattr(opt, "image_url", None) or getattr(opt, "imageUrl", None) or getattr(opt, "image", None)
        if img:
            out["image_url"] = img
        return out

    # Generic object with attributes
    if hasattr(opt, "text") or hasattr(opt, "label"):
        text = str(getattr(opt, "text", getattr(opt, "label", ""))).strip()
        out: Dict[str, Any] = {"text": text}
        img = getattr(opt, "image_url", None) or getattr(opt, "imageUrl", None) or getattr(opt, "image", None)
        if img:
            out["image_url"] = img
        return out

    # Fallback: try to stringify (last resort)
    return {"text": str(opt).strip()}


def _norm_text_key(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().casefold()


def _normalize_options(raw: List[Any], max_options: Optional[int] = None) -> List[Dict[str, Any]]:
    """Coerce to [{'text', 'image_url'?}], dedupe by normalized text (case/space-insensitive), prefer keeping media."""
    # Coerce everything into simple dicts
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

    # Dedupe while preserving order; if a later dup has an image_url and earlier doesn't, upgrade it
    seen: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for item in coerced:
        key = _norm_text_key(item["text"])
        if key not in seen:
            seen[key] = item
            order.append(key)
        else:
            existing = seen[key]
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
    # strip any existing "quiz" prefix variants like "quiz -", "Quiz —", etc.
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
        out.title = _ensure_quiz_prefix(out.title)
        logger.info("tool.generate_category_synopsis.ok", title=out.title)
        return out
    except Exception as e:
        logger.error("tool.generate_category_synopsis.fail", error=str(e), exc_info=True)
        return Synopsis(title=f"Quiz: {analysis['normalized_category']}", summary="")


# ---------------------------------------------------------------------------
# NEW: Batch character profile drafting (preferred path to cut LLM calls)
# ---------------------------------------------------------------------------

@tool
async def draft_character_profiles(
    character_names: List[str],
    category: str,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> List[CharacterProfile]:
    """
    Draft profiles for multiple outcomes in one call.
    Uses optional RAG snippets only if policy allows; otherwise passes empty context.

    NOTE: Graph should try this first for efficiency; fall back to per-item generation
    only if this tool errors or returns nothing.
    """
    logger.info(
        "tool.draft_character_profiles.start",
        category=category,
        count=len(character_names or []),
    )

    # Analyze topic (deterministic; no I/O)
    analysis = _analyze_topic(category)

    # Best-effort per-character context (OPTIONAL, policy-gated); keep cheap & bounded.
    contexts: Dict[str, str] = {}
    if (analysis["is_media"] or analysis["creativity_mode"] == "factual") and character_names:
        for name in character_names:
            try:
                ctx = await _fetch_character_context(
                    name,
                    analysis["normalized_category"],
                    trace_id,
                    session_id,
                )
                if ctx:
                    contexts[name] = ctx
            except Exception as e:
                logger.debug("tool.draft_character_profiles.ctx_skip", character=name, reason=str(e))

    # Invoke batch writer prompt (returns an array of CharacterProfile JSON objects)
    prompt = prompt_manager.get_prompt("profile_batch_writer")
    messages = prompt.invoke(
        {
            "category": analysis["normalized_category"],
            "outcome_kind": analysis["outcome_kind"],
            "creativity_mode": analysis["creativity_mode"],
            "character_contexts": contexts,  # may be {}
        }
    ).messages

    # The LLM service expects a Pydantic model for structured_response; since this is a list,
    # fetch text and parse robustly into List[CharacterProfile] with a TypeAdapter.
    try:
        raw = await llm_service.get_text_response(
            tool_name="profile_batch_writer",
            messages=messages,
            trace_id=trace_id,
            session_id=session_id,
        ).__await__()  # explicit await of coroutine returned by get_text_response
    except Exception as e:
        logger.error("tool.draft_character_profiles.invoke_fail", error=str(e), exc_info=True)
        return []

    # Strip fenced blocks if present
    fenced = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)
    if isinstance(raw, str):
        m = fenced.match(raw.strip())
        if m:
            raw = m.group(1)

    data: Any
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        logger.warning("tool.draft_character_profiles.json_parse_fallback")
        data = []

    try:
        adapter = TypeAdapter(List[CharacterProfile])
        objs: List[CharacterProfile] = adapter.validate_python(data)
    except ValidationError as e:
        logger.error("tool.draft_character_profiles.validation", error=str(e), exc_info=True)
        return []

    # Name lock safety (ensure names match the requested labels when possible)
    fixed: List[CharacterProfile] = []
    for want, got in zip(character_names or [], objs or []):
        try:
            if (got.name or "").strip().casefold() != (want or "").strip().casefold():
                fixed.append(
                    CharacterProfile(
                        name=want,
                        short_description=got.short_description,
                        profile_text=got.profile_text,
                        image_url=getattr(got, "image_url", None),
                    )
                )
            else:
                fixed.append(got)
        except Exception:
            fixed.append(CharacterProfile(name=want, short_description="", profile_text=""))

    logger.info("tool.draft_character_profiles.ok", returned=len(fixed))
    return fixed


# ---------------------------------------------------------------------------
# Single-profile drafting (kept as fallback)
# ---------------------------------------------------------------------------

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

    Fallback: Graph should prefer draft_character_profiles for efficiency and only
    call this per-item path when batch fails or a single profile must be regenerated.

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
    num_questions: Optional[int] = None,  # <-- accept optional override; aligns with graph.py
) -> List[QuizQuestion]:
    """Generate N baseline questions in one structured call."""
    n = int(num_questions) if isinstance(num_questions, int) and num_questions > 0 else getattr(settings.quiz, "baseline_questions_n", 5)
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
            # back-compat; harmless surplus variable for templates that accept it
            "normalized_category": analysis["normalized_category"],
        }
    ).messages

    qlist = await llm_service.get_structured_response(
        tool_name="question_generator",
        messages=messages,
        response_model=QuestionList,
        trace_id=trace_id,
        session_id=session_id,
    )

    # Trim to requested count (fix: respect num_questions override)
    questions = list(getattr(qlist, "questions", []))[: max(n, 0)]

    out: List[QuizQuestion] = []
    for q in questions:
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
        return out  # FinalResult Pydantic model
    except Exception as e:
        logger.error("tool.write_final_user_profile.fail", error=str(e), exc_info=True)
        return FinalResult(
            title="We couldn't determine your result",
            description="Please try again with a different topic.",
            image_url=None,
        )
