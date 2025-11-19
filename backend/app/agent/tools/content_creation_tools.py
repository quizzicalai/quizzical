# backend/app/agent/tools/content_creation_tools.py
"""
Agent Tools: Content Creation (zero-knowledge, deterministic shapes)

Responsibilities:
- Generate synopsis (title + summary)
- Draft character profiles (batch-first + single)
- Create baseline and adaptive questions
- Decide whether to continue or finish
- Write the final result

Key guarantees:
- NO retrieval/RAG or canon checks here (pure generation).
- Strict, schema-aligned structured outputs via invoke_structured(..).
- image_url is preserved on options when present.
- Stable fallbacks on any model/provider drift.

Aligned with:
- app.agent.graph (calls & payload shapes)
- app.agent.schemas (Pydantic models + jsonschema_for(..))
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import structlog
from langchain_core.tools import tool
from pydantic import ValidationError
from pydantic.type_adapter import TypeAdapter

# Centralized structured LLM invocation
from app.agent.llm_helpers import invoke_structured
from app.agent.prompts import prompt_manager
from app.agent.schemas import (
    CharacterProfile,
    NextStepDecision,
    QuestionList,
    QuestionOut,
    QuizQuestion,
    jsonschema_for,
)

# Local, data-driven topic/intent analysis (no network)
from app.agent.tools.intent_classification import analyze_topic
from app.core.config import settings
from app.models.api import FinalResult

logger = structlog.get_logger(__name__)

__all__ = [
    "draft_character_profiles",
    "draft_character_profile",
    "generate_baseline_questions",
    "generate_next_question",
    "decide_next_step",
    "write_final_user_profile",
]

# =============================================================================
# Config helpers
# =============================================================================

def _deep_get(obj: Any, path: List[str], default=None):
    """Safe nested getter for objects or dicts."""
    cur = obj
    for p in path:
        if cur is None:
            return default
        try:
            cur = cur[p] if isinstance(cur, dict) else getattr(cur, p, None)
        except Exception:
            return default
    return cur if cur is not None else default


def _quiz_cfg_get(name: str, default: Any) -> Any:
    """
    Read quiz config from either settings.quiz.* or settings.quizzical.quiz.*.
    Matches your YAML layout in appconfig.local.yaml.
    """
    for path in (["quiz", name], ["quizzical", "quiz", name]):
        val = _deep_get(settings, path, None)
        if val is not None:
            return val
    return default


# =============================================================================
# Topic analysis helpers
# =============================================================================

def _analyze_topic_safe(category: str, synopsis: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Call analyze_topic with backwards-compatible signature handling."""
    try:
        return analyze_topic(category, synopsis)
    except TypeError:
        return analyze_topic(category)


def _resolve_analysis(
    category: str,
    synopsis: Optional[Dict[str, Any]] = None,
    analysis: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Use provided analysis if valid; otherwise compute locally."""
    if isinstance(analysis, dict) and analysis.get("normalized_category"):
        return analysis
    return _analyze_topic_safe(category, synopsis)


# =============================================================================
# Options normalization (preserve image_url)
# =============================================================================

_FILLERS = [{"text": "Yes"}, {"text": "No"}, {"text": "Maybe"}, {"text": "Skip"}]


def _option_to_dict(opt: Any) -> Dict[str, Any]:
    """
    Coerce option (str | dict | pydantic | dataclass | object-with-text) → {'text', 'image_url'?}.
    Avoid using str(opt) on whole objects to prevent repr leakage.
    """
    if isinstance(opt, str):
        return {"text": opt.strip()}

    if isinstance(opt, dict):
        text = (opt.get("text") or opt.get("label") or opt.get("option") or "").strip()
        out: Dict[str, Any] = {"text": text}
        img = opt.get("image_url") or opt.get("imageUrl") or opt.get("image")
        if isinstance(img, str) and img.strip():
            out["image_url"] = img.strip()
        return out

    if hasattr(opt, "model_dump"):
        data = opt.model_dump()
        text = str(data.get("text") or data.get("label") or data.get("option") or "").strip()
        out = {"text": text}
        img = data.get("image_url") or data.get("imageUrl") or data.get("image")
        if isinstance(img, str) and img.strip():
            out["image_url"] = img.strip()
        return out

    if hasattr(opt, "__dataclass_fields__"):
        text = str(getattr(opt, "text", getattr(opt, "label", getattr(opt, "option", "")))).strip()
        out = {"text": text}
        img = getattr(opt, "image_url", None) or getattr(opt, "imageUrl", None) or getattr(opt, "image", None)
        if isinstance(img, str) and img.strip():
            out["image_url"] = img.strip()
        return out

    if hasattr(opt, "text") or hasattr(opt, "label") or hasattr(opt, "option"):
        text = str(getattr(opt, "text", getattr(opt, "label", getattr(opt, "option", "")))).strip()
        out = {"text": text}
        img = getattr(opt, "image_url", None) or getattr(opt, "imageUrl", None) or getattr(opt, "image", None)
        if isinstance(img, str) and img.strip():
            out["image_url"] = img.strip()
        return out

    return {"text": str(opt).strip()}


def _norm_text_key(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().casefold()


def _normalize_options(raw: List[Any], max_options: Optional[int] = None) -> List[Dict[str, Any]]:
    """
    Coerce to [{'text','image_url'?}], dedupe by text (case/space-insensitive),
    prefer keeping media, and cap at max_options when set.
    """
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
            # Upgrade with image_url if the later duplicate has one
            if not seen[key].get("image_url") and item.get("image_url"):
                seen[key]["image_url"] = item["image_url"]

    uniq = [seen[k] for k in order]
    if max_options and max_options > 0:
        uniq = uniq[:max_options]
    return uniq


def _ensure_min_options(options: List[Dict[str, Any]], minimum: int = 2) -> List[Dict[str, Any]]:
    """
    Ensure each question has at least `minimum` options.
    Filters malformed entries, omits falsy image_url, pads deterministically.
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

    need = max(0, minimum - len(clean))
    return clean + _FILLERS[:need]

# =============================================================================
# Tools
# =============================================================================

@tool(description="Draft multiple character profiles in one structured call (no retrieval).")
async def draft_character_profiles(
    character_names: List[str],
    category: str,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
    analysis: Optional[Dict[str, Any]] = None,
) -> List[CharacterProfile]:
    """
    Draft profiles for multiple outcomes in one call.
    ZERO-KNOWLEDGE: No retrieval or canon checks; produce self-consistent bios.
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

    prompt = prompt_manager.get_prompt("profile_batch_writer")
    messages = prompt.invoke(
        {
            "category": analysis["normalized_category"],
            "outcome_kind": analysis["outcome_kind"],
            "creativity_mode": analysis["creativity_mode"],
            "intent": analysis.get("intent", "identify"),
            "character_contexts": {},  # intentionally empty under zero-knowledge strategy
            "character_names": character_names,
            "count": count,
        }
    ).messages

    # Strict list validation using TypeAdapter[List[CharacterProfile]]
    try:
        adapter = TypeAdapter(List[CharacterProfile])
        objs: List[CharacterProfile] = await invoke_structured(
            tool_name="profile_batch_writer",
            messages=messages,
            response_model=adapter,
            explicit_schema=jsonschema_for("profile_batch_writer"),
            trace_id=trace_id,
            session_id=session_id,
        )
    except Exception as e:
        logger.error("tool.draft_character_profiles.validation_or_invoke_fail", error=str(e), exc_info=True)
        return []

    # Name-lock & size-correct (preserve order)
    fixed: List[CharacterProfile] = []
    for idx, want in enumerate(character_names):
        got = objs[idx] if idx < len(objs) else None
        if got is None:
            fixed.append(CharacterProfile(name=want, short_description="", profile_text=""))
            continue
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


@tool(description="Draft a single character profile (no retrieval; coherent, self-contained bio).")
async def draft_character_profile(
    character_name: str,
    category: str,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
    analysis: Optional[Dict[str, Any]] = None,
) -> CharacterProfile:
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
            "character_context": "",  # intentionally empty
            "normalized_category": analysis["normalized_category"],  # back-compat
        }
    ).messages

    try:
        out: CharacterProfile = await invoke_structured(
            tool_name="profile_writer",
            messages=messages,
            response_model=CharacterProfile,
            explicit_schema=jsonschema_for("profile_writer"),
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


@tool(description="Generate a batch of baseline multiple-choice questions deterministically.")
async def generate_baseline_questions(
    category: str,
    character_profiles: List[Dict[str, Any]],
    synopsis: Dict[str, Any],
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
    analysis: Optional[Dict[str, Any]] = None,
    num_questions: Optional[int] = None,
) -> List[QuizQuestion]:
    """Generate N baseline questions in one structured call (zero-knowledge)."""
    n = int(num_questions) if isinstance(num_questions, int) and num_questions > 0 else _quiz_cfg_get(
        "baseline_questions_n", 5
    )
    m = _quiz_cfg_get("max_options_m", 4)

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
            "normalized_category": analysis["normalized_category"],
        }
    ).messages

    # Primary path: strict QuestionList
    try:
        qlist: QuestionList = await invoke_structured(
            tool_name="question_generator",
            messages=messages,
            response_model=QuestionList,
            explicit_schema=jsonschema_for("question_generator", count=n, max_options=m),
            trace_id=trace_id,
            session_id=session_id,
        )
        questions_raw = list(getattr(qlist, "questions", []) or [])[: max(n, 0)]
    except Exception as e:
        logger.error("tool.generate_baseline_questions.fail", error=str(e), exc_info=True)
        questions_raw = []

    out: List[QuizQuestion] = []
    for q in questions_raw:
        # Access options whether q is a Pydantic object or dict
        opts_raw = getattr(q, "options", None)
        if opts_raw is None and isinstance(q, dict):
            opts_raw = q.get("options", [])
        opts = _normalize_options(opts_raw or [], max_options=m)
        opts = _ensure_min_options(opts, minimum=2)

        # question_text for both object and dict
        qt_attr = getattr(q, "question_text", None)
        qt = qt_attr if isinstance(qt_attr, str) and qt_attr.strip() else ""
        if not qt and isinstance(q, dict):
            qt = str(q.get("question_text") or "").strip()
        qt = qt or "Baseline question"

        out.append(QuizQuestion(question_text=qt, options=opts))

    logger.info("tool.generate_baseline_questions.ok", count=len(out))
    return out


@tool(description="Generate one adaptive next question based on prior answers (zero-knowledge).")
async def generate_next_question(
    quiz_history: List[Dict[str, Any]],
    character_profiles: List[Dict[str, Any]],
    synopsis: Dict[str, Any],
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
    analysis: Optional[Dict[str, Any]] = None,
) -> QuizQuestion:
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

    m = _quiz_cfg_get("max_options_m", 4)
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
            explicit_schema=jsonschema_for("next_question_generator", max_options=m),
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


@tool(description="Decide whether to ask one more question or finish now based on quiz history.")
async def decide_next_step(
    quiz_history: List[Dict[str, Any]],
    character_profiles: List[Dict[str, Any]],
    synopsis: Dict[str, Any],
    analysis: Optional[Dict[str, Any]] = None,
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> NextStepDecision:
    """Decide whether to ask one more question or finish now (no disambiguation flow)."""

    def _to_dict(x: Any) -> Any:
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
            "min_questions_before_finish": _quiz_cfg_get("min_questions_before_early_finish", 6),
            "confidence_threshold": _quiz_cfg_get("early_finish_confidence", 0.9),
            "max_total_questions": _quiz_cfg_get("max_total_questions", 20),
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
        explicit_schema=jsonschema_for("decision_maker"),
        trace_id=trace_id,
        session_id=session_id,
    )


@tool(description="Write the final, personalized quiz result for the user.")
async def write_final_user_profile(
    winning_character: Dict[str, Any],
    quiz_history: List[Dict[str, Any]],
    trace_id: Optional[str] = None,
    session_id: Optional[str] = None,
    # Graph passes these explicitly; we fall back to character fields, then defaults.
    category: Optional[str] = None,
    outcome_kind: Optional[str] = None,
    creativity_mode: Optional[str] = None,
) -> FinalResult:
    logger.info("tool.write_final_user_profile.start", character=winning_character.get("name"))

    _category = (category or winning_character.get("category") or "").strip()
    _outcome_kind = (outcome_kind or "types").strip()
    _creativity_mode = (creativity_mode or "balanced").strip()

    prompt = prompt_manager.get_prompt("final_profile_writer")
    messages = prompt.invoke(
        {
            "winning_character_name": winning_character.get("name"),
            "quiz_history": quiz_history,
            "category": _category,
            "creativity_mode": _creativity_mode,
            "outcome_kind": _outcome_kind,
            "intent": winning_character.get("intent") or "identify",
        }
    ).messages

    try:
        out: FinalResult = await invoke_structured(
            tool_name="final_profile_writer",
            messages=messages,
            response_model=FinalResult,
            explicit_schema=jsonschema_for("final_profile_writer"),
            trace_id=trace_id,
            session_id=session_id,
        )

        # Post-process / harden
        t = (out.title or "").strip() or f"You are {winning_character.get('name','Someone great')}!"
        d = (out.description or "").strip()
        img = out.image_url or winning_character.get("image_url")  # inherit if model returned null

        out.title = t
        out.description = d
        out.image_url = img if isinstance(img, str) and img.strip() else None

        logger.info("tool.write_final_user_profile.ok")
        return out

    except Exception as e:
        logger.error("tool.write_final_user_profile.fail", error=str(e), exc_info=True)
        # Graceful, schema-valid fallback
        fallback_title = f"You are {winning_character.get('name','Your Best Self')}!"
        why = "Your answers consistently aligned with this profile."
        return FinalResult(title=fallback_title, description=why, image_url=winning_character.get("image_url"))
