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
from typing import Any

import structlog
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from pydantic import ValidationError
from pydantic.type_adapter import TypeAdapter

# Centralized structured LLM invocation
from app.agent.instrument_rigor import InstrumentSpec, instrument_spec_for
from app.agent.llm_helpers import invoke_structured
from app.agent.progress_phrases import (
    baseline_phrase_for_index,
    pick_progress_phrase,
)
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
from app.models.api import BlendedDimension, BlendedProfile, FinalResult

logger = structlog.get_logger(__name__)

__all__ = [
    "draft_character_profiles",
    "draft_character_profile",
    "generate_baseline_questions",
    "generate_next_question",
    "decide_next_step",
    "write_final_user_profile",
    "write_blended_profile",
    "is_self_referential_question",
    "canonical_hint_block",
]

# =============================================================================
# Config helpers
# =============================================================================

def _deep_get(obj: Any, path: list[str], default=None):
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


def _effective_depth_floor_cap(category: str | None) -> tuple[int, int]:
    """Topic-aware (effective floor, hard cap) for question depth.

    Mirrors ``graph._effective_depth_bounds`` so the decision_maker prompt quotes
    the SAME per-topic floor the graph gate enforces. Rigorous instruments raise
    the floor via the canonical catalog / App-Config ``min_items``; casual topics
    use the global floor. The cap never exceeds the owner ceiling of 24.
    """
    global_floor = int(_quiz_cfg_get("min_questions_before_early_finish", 12))
    floor_min = int(_quiz_cfg_get("depth_floor_min", 12))
    hard_max = min(int(_quiz_cfg_get("max_total_questions", 24)), 24)

    per_instrument = 0
    try:
        from app.agent.canonical_sets import min_items_for  # local import avoids cycle

        mi = min_items_for(category)
        if isinstance(mi, int) and mi > 0:
            per_instrument = mi
    except Exception:
        per_instrument = 0

    eff_min = max(floor_min, min(max(global_floor, per_instrument), hard_max))
    return eff_min, hard_max


# =============================================================================
# Topic analysis helpers
# =============================================================================

def _analyze_topic_safe(category: str, synopsis: dict[str, Any] | None = None) -> dict[str, Any]:
    """Call analyze_topic with backwards-compatible signature handling."""
    try:
        return analyze_topic(category, synopsis)
    except TypeError:
        return analyze_topic(category)


def _resolve_analysis(
    category: str,
    synopsis: dict[str, Any] | None = None,
    analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Use provided analysis if valid; otherwise compute locally."""
    if isinstance(analysis, dict) and analysis.get("normalized_category"):
        return analysis
    return _analyze_topic_safe(category, synopsis)


# =============================================================================
# Options normalization (preserve image_url)
# =============================================================================

_FILLERS = [{"text": "Yes"}, {"text": "No"}, {"text": "Maybe"}, {"text": "Skip"}]


def _option_to_dict(opt: Any) -> dict[str, Any]:
    """
    Coerce option (str | dict | pydantic | dataclass | object-with-text) → {'text', 'image_url'?}.
    Avoid using str(opt) on whole objects to prevent repr leakage.
    """
    if isinstance(opt, str):
        return {"text": opt.strip()}

    if isinstance(opt, dict):
        text = (opt.get("text") or opt.get("label") or opt.get("option") or "").strip()
        out: dict[str, Any] = {"text": text}
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


def _normalize_options(raw: list[Any], max_options: int | None = None) -> list[dict[str, Any]]:
    """
    Coerce to [{'text','image_url'?}], dedupe by text (case/space-insensitive),
    prefer keeping media, and cap at max_options when set.
    """
    coerced: list[dict[str, Any]] = []
    for opt in (raw or []):
        d = _option_to_dict(opt)
        text = str(d.get("text") or "").strip()
        if not text:
            continue
        item: dict[str, Any] = {"text": text}
        if d.get("image_url"):
            item["image_url"] = d["image_url"]
        coerced.append(item)

    seen: dict[str, dict[str, Any]] = {}
    order: list[str] = []
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


def _ensure_min_options(options: list[dict[str, Any]], minimum: int = 2) -> list[dict[str, Any]]:
    """
    Ensure each question has at least `minimum` options.
    Filters malformed entries, omits falsy image_url, pads deterministically.
    """
    clean: list[dict[str, Any]] = []
    for o in options or []:
        if not isinstance(o, dict):
            continue
        text = str(o.get("text") or "").strip()
        if not text:
            continue
        out: dict[str, Any] = {"text": text}
        img = o.get("image_url")
        if isinstance(img, str) and img.strip():
            out["image_url"] = img.strip()
        clean.append(out)

    if len(clean) >= minimum:
        return clean

    need = max(0, minimum - len(clean))
    return clean + _FILLERS[:need]


# =============================================================================
# Self-referential / meta question guard (AC-QUALITY-SELFMATCH-1)
# =============================================================================
#
# The whole point of the quiz is that the AGENT infers the user's match from
# ordinary preference/personality answers. A question that asks the user to
# self-identify ("Which of these characters do you feel you match with?"),
# guess/rank their own outcome, or that talks about the quiz/result itself
# completely defeats that. The prompts forbid this explicitly, but models
# still emit such a question occasionally — so we ALSO detect it at runtime
# and drop (baseline) or regenerate (adaptive) it.

# Phrases that signal the user is being asked to pick/guess/rank their own
# outcome or that the question is about the quiz/result itself. These are
# matched case-insensitively as substrings against the normalized question
# text (and, where noted, the option texts).
_SELF_MATCH_PHRASES: tuple[str, ...] = (
    "which character are you",
    "which character do you think you are",
    "which of these characters",
    "character do you feel you match",
    "character you match",
    "do you match with",
    "do you most identify with",
    "which one do you identify with",
    "which do you identify with",
    "which result",
    "what result do you",
    "which outcome are you",
    "what outcome do you",
    "which type are you",
    "which type do you think you are",
    "do you think you'll get",
    "do you think you will get",
    "do you think you are most like",
    "who do you think you are most like",
    "which of the following best matches you",
    "predict your result",
    "guess your result",
    "rank these characters",
    "rank the characters",
    "rank the outcomes",
    "how accurate is this quiz",  # meta about the quiz
    "how accurate do you think",  # meta about the quiz
)

# Regex catches the self-identification shape ("which … do you … match /
# identify with / relate to / resemble") with a DELIBERATELY narrow verb set
# so ordinary preference questions ("Which activity would you choose?") do NOT
# trip it. Only verbs that signal the user is being asked about their own
# match/identity are included — never generic choice verbs like pick/choose/get.
_SELF_MATCH_RE = re.compile(
    r"(?i)\b(which|what|who)\b.{0,80}\b(you)\b"
    r".{0,40}\b(match|matches|identify|identifies|relate|relates|resemble|resembles)\b"
)


# Common English words that double as outcome/character names. A bare
# whole-word match of one of these in a question is almost always the ordinary
# word, not the outcome ("What do you HOPE to achieve?", "Where do you find
# GRACE under pressure?"), so it must NOT, on its own, flag the question.
# Casefolded; membership is checked after `_norm_text_key`.
_COMMON_WORD_NAME_STOPLIST: frozenset[str] = frozenset(
    {
        "will", "hope", "grace", "may", "art", "sky", "faith", "joy", "dawn",
        "rose", "summer", "autumn", "april", "june", "rain", "river", "star",
        "angel", "honor", "honour", "victory", "justice", "destiny", "melody",
        "harmony", "max", "bill", "drew", "mark", "rich", "frank", "earnest",
    }
)

# Bare candidate names shorter than this are too collision-prone to treat as a
# self-reference signal on their own (raised 3 -> 4 for #7).
_MIN_BARE_NAME_LEN: int = 4


def _contains_name(haystack: str, name_key: str) -> bool:
    """Whole-word/phrase containment so short candidate names (e.g. 'Ron',
    'Sam', 'Cat') don't match INSIDE ordinary words ('wrong', 'same',
    'category') and wrongly flag a legitimate question. `name_key`/`haystack`
    are already passed through ``_norm_text_key`` (casefolded, whitespace
    collapsed; punctuation preserved), so word boundaries are reliable."""
    if not name_key:
        return False
    return re.search(rf"\b{re.escape(name_key)}\b", haystack) is not None


def is_self_referential_question(  # noqa: C901 — linear detection layers (phrase + regex + name-as-word guards)
    question_text: str | None,
    options: list[dict[str, Any]] | None = None,
    character_names: list[str] | None = None,
) -> bool:
    """Return True if the question asks the user to self-identify / is meta.

    Detects the failure mode where the model asks the user which candidate
    character/outcome they think they match, asks them to rank/guess their own
    result, or asks a meta question about the quiz/result itself. Such a
    question must never reach the user — the agent is supposed to infer the
    match from ordinary answers.

    Detection layers (any one is sufficient):
      1. A known self-identification / meta phrase appears in the question.
      2. The generic "which … do you … match/are" regex matches.
      3. A candidate character/outcome NAME appears verbatim in the question
         text or in an OPTION (offering the outcomes themselves as answers is
         the most blatant form of the bug).
    """
    qt = _norm_text_key(question_text or "")
    if not qt:
        return False

    matched_phrase_or_regex = False
    for phrase in _SELF_MATCH_PHRASES:
        if phrase in qt:
            matched_phrase_or_regex = True
            break
    if not matched_phrase_or_regex and _SELF_MATCH_RE.search(qt):
        matched_phrase_or_regex = True
    if matched_phrase_or_regex:
        return True

    # Naming the candidate outcomes (in the question or as the answer options)
    # is the most blatant form of "pick your own result". Two name sets (#7):
    #
    #   * QUESTION-TEXT signal uses DISTINCTIVE names only (>=4 chars and NOT a
    #     common English word). A common-word outcome (Will/Hope/Grace/May)
    #     appearing as an ordinary word in a legit question ("What do you HOPE
    #     to achieve?") must not flag. But a single DISTINCTIVE outcome name in
    #     the question ("Are you more of a Gryffindor?") IS the bug, so a single
    #     distinctive name is sufficient.
    #   * OPTIONS signal uses a LESS-filtered set (>=3 chars, common-word names
    #     INCLUDED): an outcome name appearing as a discrete ANSWER OPTION is
    #     blatant regardless of common-word status (offering "Hope"/"Will" as
    #     options is the model literally listing the outcomes). We still require
    #     2+ distinct names as options so a single coincidental short option
    #     doesn't trip it.
    #
    # Whole-word matching is preserved throughout.
    def _dedupe(keys: list[str]) -> list[str]:
        seen: set[str] = set()
        return [k for k in keys if not (k in seen or seen.add(k))]

    distinctive_keys = _dedupe([
        nkey
        for n in (character_names or [])
        if isinstance(n, str)
        and (nkey := _norm_text_key(n))
        and len(nkey) >= _MIN_BARE_NAME_LEN
        and nkey not in _COMMON_WORD_NAME_STOPLIST
    ])
    option_name_keys = _dedupe([
        nkey
        for n in (character_names or [])
        if isinstance(n, str)
        and (nkey := _norm_text_key(n))
        and len(nkey) >= 3
    ])

    # QUESTION text: a single distinctive outcome name is enough.
    if any(_contains_name(qt, k) for k in distinctive_keys):
        return True

    # OPTIONS: 2+ distinct candidate names offered as answers.
    if option_name_keys:
        option_texts = [
            _norm_text_key(str((o or {}).get("text") or ""))
            for o in (options or [])
            if isinstance(o, dict)
        ]
        names_as_options = sum(
            1 for k in option_name_keys if any(_contains_name(ot, k) for ot in option_texts)
        )
        if names_as_options >= 2:
            return True
    return False


def _question_dimension(q: Any, instrument: "InstrumentSpec | None") -> str | None:
    """Normalized instrument-dimension tag for a generated question, or None.

    Only meaningful when the topic resolved to a validated instrument — for
    every other topic this returns None so the question dict is byte-identical
    to today's output (``dimension`` is excluded on dump when None). A loose
    model label ("e/i", the dimension name, …) is snapped onto the canonical
    code; an unrecognized label is dropped rather than mis-counted.
    """
    if instrument is None:
        return None
    raw = getattr(q, "dimension", None)
    if raw is None and isinstance(q, dict):
        raw = q.get("dimension")
    return instrument.normalize_code(raw)


def _character_names_from_profiles(
    character_profiles: list[Any] | None,
) -> list[str]:
    """Extract candidate outcome names from profile dicts/models."""
    names: list[str] = []
    for c in character_profiles or []:
        if isinstance(c, dict):
            n = c.get("name")
        else:
            n = getattr(c, "name", None)
        if isinstance(n, str) and n.strip():
            names.append(n.strip())
    return names


def canonical_hint_block(category: str | None, character_names: list[str]) -> str:
    """Render 1-line canonical grounding hints for the batch profile prompt.

    The 2026-07-01 PBW eval found quality capped at 2.81/5 with the root cause
    "zero grounding": ``character_contexts`` was always ``{}`` so the model
    wrote every profile from the name alone. When the topic resolves to a
    reviewed canonical set (``canonical_sets`` catalog: Hogwarts Houses, MBTI,
    zodiac, …) we now feed one line per name telling the model this outcome is
    a REAL, widely known member of that set and to ground the profile in its
    recognised traits instead of inventing generic filler.

    Returns "" (prompt-visible as empty context) when the topic does not
    resolve canonically or none of the requested names belong to the set —
    non-canonical topics keep today's zero-knowledge behaviour byte-for-byte.
    Names not in the set (e.g. model-invented extras on a canonical topic) get
    no hint rather than a wrong one.
    """
    if not character_names:
        return ""
    try:
        from app.agent.canonical_sets import (  # local import avoids cycle
            canonical_for,
            canonical_title_for,
        )

        title = canonical_title_for(category)
        names = canonical_for(category) if title else None
    except Exception:
        return ""
    if not title or not names:
        return ""

    canon_by_key = {n.strip().casefold(): n for n in names}
    total = len(names)
    lines: list[str] = []
    for want in character_names:
        key = (want or "").strip().casefold()
        canon = canon_by_key.get(key)
        if canon is None:
            continue
        siblings = [n for n in names if n != canon][:6]
        sibling_note = f" (alongside {', '.join(siblings)})" if siblings else ""
        lines.append(
            f"- {want}: one of the {total} canonical members of '{title}'"
            f"{sibling_note}. Ground this profile in the real, widely "
            f"recognised traits of {want}; stay true to how it is commonly "
            "understood and make it clearly distinct from its siblings."
        )
    return "\n".join(lines)


def _map_profiles_to_names(
    character_names: list[str],
    objs: list[CharacterProfile],
) -> list[CharacterProfile]:
    """Map model-returned profiles onto the requested roster, never dropping a name.

    The batch writer must emit exactly one profile per requested name, but a
    model can (a) return them out of order, (b) skip a name, or (c) return a
    profile whose ``name`` does not match any requested name. We therefore:

      1. Index the returned profiles by case-folded name.
      2. For each requested name, prefer the name-matched profile; if none
         exists, fall back to the positional profile (legacy behaviour) so a
         simple reordering still works; otherwise synthesise an empty profile
         so the name is NEVER silently lost.
      3. Emit a ``tool.draft_character_profiles.missing_names`` guard log
         whenever any name had to be back-filled empty, so a real coverage
         regression (e.g. output truncation) is observable instead of silent.

    The returned list always has exactly ``len(character_names)`` entries, in
    the requested order, with ``name`` locked to the requested spelling.
    """
    by_name: dict[str, CharacterProfile] = {}
    for o in objs:
        key = (getattr(o, "name", "") or "").strip().casefold()
        if key and key not in by_name:
            by_name[key] = o

    fixed: list[CharacterProfile] = []
    missing: list[str] = []
    for idx, want in enumerate(character_names):
        key = (want or "").strip().casefold()
        got = by_name.get(key)
        if got is None and idx < len(objs):
            # Positional fallback: a profile exists at this slot but its name
            # did not match (e.g. a near-miss spelling). Reuse its content.
            got = objs[idx]
        if got is None:
            missing.append(want)
            fixed.append(CharacterProfile(name=want, short_description="", profile_text=""))
            continue
        try:
            short_desc = getattr(got, "short_description", "") or ""
            profile_text = getattr(got, "profile_text", "") or ""
            # An empty profile_text means the name is present but uncovered;
            # treat it as a coverage miss for the guard, but keep what we have.
            if not profile_text.strip():
                missing.append(want)
            if (got.name or "").strip().casefold() != key:
                fixed.append(
                    CharacterProfile(
                        name=want,
                        short_description=short_desc,
                        profile_text=profile_text,
                        image_url=getattr(got, "image_url", None),
                    )
                )
            else:
                # Name already matches the request; lock the spelling to the
                # requested form anyway so casing drift cannot leak downstream.
                got.name = want
                fixed.append(got)
        except Exception:
            missing.append(want)
            fixed.append(CharacterProfile(name=want, short_description="", profile_text=""))

    if missing:
        logger.warning(
            "tool.draft_character_profiles.missing_names",
            requested=len(character_names),
            returned=len(objs),
            missing_count=len(missing),
            missing_names=missing,
        )

    return fixed

# =============================================================================
# Tools
# =============================================================================

@tool(description="Draft multiple character profiles in one structured call (no retrieval).")
async def draft_character_profiles(
    character_names: list[str],
    category: str,
    trace_id: str | None = None,
    session_id: str | None = None,
    analysis: dict[str, Any] | None = None,
) -> list[CharacterProfile]:
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

    # Render the roster as an explicit numbered enumeration so the prompt
    # states the exact count AND lists every required name verbatim. A raw
    # ``str(list)`` repr is easy for a model to under-count on long batches;
    # an enumerated "1. Name" block keeps each required output salient.
    enumerated_names = "\n".join(
        f"{i}. {name}" for i, name in enumerate(character_names, start=1)
    )

    # Canonical grounding (AC-EVAL-2026-07-02): when the topic resolves to a
    # reviewed canonical set, feed a 1-line hint per name so profiles are
    # grounded in the real member instead of written from the name alone.
    # Empty for non-canonical topics (unchanged zero-knowledge behaviour).
    hints = canonical_hint_block(
        analysis.get("normalized_category") or category, character_names
    ) or canonical_hint_block(category, character_names)
    if hints:
        logger.info(
            "tool.draft_character_profiles.canonical_hints",
            category=category,
            hinted=hints.count("\n- ") + 1,
        )

    prompt = prompt_manager.get_prompt("profile_batch_writer")
    messages = prompt.invoke(
        {
            "category": analysis["normalized_category"],
            "outcome_kind": analysis["outcome_kind"],
            "creativity_mode": analysis["creativity_mode"],
            "intent": analysis.get("intent", "identify"),
            "character_contexts": hints,  # canonical hints, or "" when unknown
            "character_names": enumerated_names,
            "count": count,
        }
    ).messages

    # Strict list validation using TypeAdapter[List[CharacterProfile]]
    try:
        adapter = TypeAdapter(list[CharacterProfile])
        objs: list[CharacterProfile] = await invoke_structured(
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

    fixed = _map_profiles_to_names(character_names, objs or [])

    logger.info("tool.draft_character_profiles.ok", returned=len(fixed))
    return fixed


@tool(description="Draft a single character profile (no retrieval; coherent, self-contained bio).")
async def draft_character_profile(
    character_name: str,
    category: str,
    trace_id: str | None = None,
    session_id: str | None = None,
    analysis: dict[str, Any] | None = None,
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
    character_profiles: list[dict[str, Any]],
    synopsis: dict[str, Any],
    trace_id: str | None = None,
    session_id: str | None = None,
    analysis: dict[str, Any] | None = None,
    num_questions: int | None = None,
) -> list[QuizQuestion]:
    """Generate N baseline questions in one structured call (zero-knowledge)."""
    n = int(num_questions) if isinstance(num_questions, int) and num_questions > 0 else _quiz_cfg_get(
        "baseline_questions_n", 5
    )
    m = _quiz_cfg_get("max_options_m", 4)

    analysis = _resolve_analysis(category, synopsis, analysis)

    # INSTRUMENT RIGOR (owner blackbox #5, 2026-07-02): when the topic resolves
    # to a validated instrument with known dimensions (MBTI, DISC, Big Five, …)
    # inject the conditional rigor block; "" for every other topic so whimsical
    # questioning is untouched. Same double-resolve pattern as canonical hints.
    instrument = instrument_spec_for(
        analysis.get("normalized_category"), category
    )
    rigor_block = instrument.render_question_block() if instrument else ""
    if instrument:
        logger.info(
            "tool.generate_baseline_questions.instrument_rigor",
            instrument=instrument.title,
            dimensions=instrument.codes,
        )

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
            "instrument_rigor": rigor_block,
            "normalized_category": analysis["normalized_category"],
        }
    ).messages

    candidate_names = _character_names_from_profiles(character_profiles)

    async def _build_batch(extra_messages: list[Any] | None) -> tuple[list[QuizQuestion], int]:
        """Generate one batch and return (surviving questions, dropped count).

        Self-referential / meta questions are dropped (not regenerated
        per-item): we have a batch, so skipping bad ones keeps cost flat and
        the surviving questions intact.
        """
        try:
            qlist: QuestionList = await invoke_structured(
                tool_name="question_generator",
                messages=[*messages, *(extra_messages or [])],
                response_model=QuestionList,
                explicit_schema=jsonschema_for("question_generator", count=n, max_options=m),
                trace_id=trace_id,
                session_id=session_id,
            )
            questions_raw = list(getattr(qlist, "questions", []) or [])[: max(n, 0)]
        except Exception as e:
            logger.error("tool.generate_baseline_questions.fail", error=str(e), exc_info=True)
            questions_raw = []

        survivors: list[QuizQuestion] = []
        dropped_local = 0
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

            if is_self_referential_question(qt, opts, candidate_names):
                dropped_local += 1
                logger.warning(
                    "tool.generate_baseline_questions.self_referential_dropped",
                    question_preview=qt[:120],
                )
                continue

            survivors.append(QuizQuestion(
                question_text=qt,
                options=opts,
                progress_phrase=baseline_phrase_for_index(len(survivors)),
                # INSTRUMENT RIGOR: carry the probed-dimension tag (normalized
                # onto the canonical code) so the adaptive path can balance
                # coverage. None for non-instrument topics (excluded on dump).
                dimension=_question_dimension(q, instrument),
            ))
        return survivors, dropped_local

    out, dropped = await _build_batch(None)

    # #6: a self-match-heavy batch can be decimated by the drop loop, leaving
    # too few baseline questions and silently degrading the quiz to the
    # (slower, pricier) adaptive path. If survivors fall below a minimum floor,
    # regenerate the batch ONCE with the adaptive-path booster instruction,
    # then keep whichever attempt yielded more questions (never fewer than the
    # first attempt). A single retry bounds the added cost to one extra call.
    min_keep = max(3, n // 2)
    if dropped > 0 and len(out) < min_keep:
        logger.warning(
            "baseline_node.self_referential_underflow",
            dropped=dropped,
            kept=len(out),
            min_keep=min_keep,
            requested=n,
        )
        booster = HumanMessage(
            content=(
                "Several of your previous questions asked the user to identify, "
                "guess, rank, or pick their own result, named the candidate "
                "outcomes, or were meta questions about the quiz. That is "
                "forbidden — it defeats the quiz. Regenerate the FULL batch of "
                f"{n} questions as ordinary preference / personality / behaviour "
                "/ situational questions whose answers let YOU infer the match. "
                "Do NOT name the candidate outcomes, do NOT ask which one they "
                "are or match, and do NOT mention the quiz or result itself."
            )
        )
        retry_out, retry_dropped = await _build_batch([booster])
        if len(retry_out) > len(out):
            logger.info(
                "baseline_node.self_referential_underflow_recovered",
                kept=len(retry_out),
                dropped=retry_dropped,
            )
            out, dropped = retry_out, retry_dropped
        # else: fall back to the original survivors (never serve fewer).

    logger.info("tool.generate_baseline_questions.ok", count=len(out), dropped=dropped)
    return out


@tool(description="Generate one adaptive next question based on prior answers (zero-knowledge).")
async def generate_next_question(  # noqa: C901 — adaptive flow: generate + self-ref guard/regenerate + safe fallback
    quiz_history: list[dict[str, Any]],
    character_profiles: list[dict[str, Any]],
    synopsis: dict[str, Any],
    trace_id: str | None = None,
    session_id: str | None = None,
    analysis: dict[str, Any] | None = None,
    asked_dimensions: list[str] | None = None,
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

    # NOTE (AC-EVAL-2026-07-02, punchlist P5): this call used to inline the
    # full ~40-phrase narrowing pool as `progress_phrase_pool` on EVERY
    # adaptive call, but no prompt template (default or App-Config override)
    # ever referenced the placeholder or asked for a `progress_phrase` field —
    # the tokens were pure waste on the hottest loop (6-12 calls/quiz) and the
    # deterministic fallback always ran anyway. The pool inlining and the dead
    # `q_out.progress_phrase` read are gone; `pick_progress_phrase` below is
    # the single (deterministic, free) source of the FE progress pill.
    # INSTRUMENT RIGOR (owner blackbox #5, 2026-07-02): for validated
    # instruments, the adaptive question must target the LEAST-COVERED
    # dimension so a finished quiz covers all of them. ``asked_dimensions``
    # carries the dimension tags of every question generated so far (the graph
    # extracts them from state); "" block for non-instrument topics.
    instrument = instrument_spec_for(
        analysis.get("normalized_category"), derived_category
    )
    rigor_block = (
        instrument.render_question_block(asked_dimensions=list(asked_dimensions or []))
        if instrument
        else ""
    )
    if instrument:
        logger.info(
            "tool.generate_next_question.instrument_rigor",
            instrument=instrument.title,
            asked_dimensions=list(asked_dimensions or []),
            under_covered=instrument.under_covered(list(asked_dimensions or [])),
        )

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
            "instrument_rigor": rigor_block,
            "normalized_category": analysis["normalized_category"],  # back-compat
        }
    ).messages

    candidate_names = _character_names_from_profiles(character_profiles)

    def _coerce(q_out: QuestionOut) -> tuple[str, list[dict[str, Any]]]:
        opts_local = [
            {"text": o.text, **({"image_url": o.image_url} if getattr(o, "image_url", None) else {})}
            for o in getattr(q_out, "options", [])
        ]
        opts_local = _normalize_options(opts_local, max_options=m)
        opts_local = _ensure_min_options(opts_local, minimum=2)
        qt_local = (getattr(q_out, "question_text", "") or "").strip() or "Next question"
        return qt_local, opts_local

    try:
        q_out: QuestionOut = await invoke_structured(
            tool_name="next_question_generator",
            messages=messages,
            response_model=QuestionOut,
            explicit_schema=jsonschema_for("next_question_generator", max_options=m),
            trace_id=trace_id,
            session_id=session_id,
        )
        qt, opts = _coerce(q_out)
        dim = _question_dimension(q_out, instrument)

        # AC-QUALITY-SELFMATCH-1: if the model asked the user to self-identify
        # / guess their own outcome / posed a meta question, regenerate ONCE
        # with a stronger instruction. A single retry (not an unbounded loop)
        # keeps the added cost to at most one extra LLM call. If the retry is
        # still self-referential we fall through to the safe fallback below
        # rather than serving a quiz-defeating question.
        if is_self_referential_question(qt, opts, candidate_names):
            logger.warning(
                "tool.generate_next_question.self_referential_retry",
                question_preview=qt[:120],
            )
            booster = HumanMessage(
                content=(
                    "Your previous question asked the user to identify, guess, rank, or "
                    "pick their own result, or was a meta question about the quiz. That is "
                    "forbidden — it defeats the quiz. Generate a DIFFERENT ordinary "
                    "preference / personality / behaviour / situational question whose "
                    "answer lets YOU infer the match. Do NOT name the candidate outcomes, "
                    "do NOT ask which one they are or match, and do NOT mention the quiz or "
                    "result itself. Return only the JSON object."
                )
            )
            try:
                q_retry: QuestionOut = await invoke_structured(
                    tool_name="next_question_generator",
                    messages=[*messages, booster],
                    response_model=QuestionOut,
                    explicit_schema=jsonschema_for("next_question_generator", max_options=m),
                    trace_id=trace_id,
                    session_id=session_id,
                )
                qt_retry, opts_retry = _coerce(q_retry)
                if not is_self_referential_question(qt_retry, opts_retry, candidate_names):
                    qt, opts = qt_retry, opts_retry
                    dim = _question_dimension(q_retry, instrument)
                    logger.info("tool.generate_next_question.self_referential_retry_ok")
                else:
                    logger.warning(
                        "tool.generate_next_question.self_referential_retry_still_bad"
                    )
                    raise ValueError("self_referential_after_retry")
            except ValueError:
                raise
            except Exception as e:  # retry call itself failed
                logger.warning(
                    "tool.generate_next_question.self_referential_retry_failed",
                    error=str(e),
                )
                raise

        # progress_phrase: deterministic pick from the curated pool, keyed on
        # how far along we are. (The LLM was never asked for this field — see
        # the AC-EVAL-2026-07-02 note above — so the deterministic path IS the
        # behaviour users have always seen; it is now also the only path.)
        answered = len(quiz_history or [])
        max_total = int(_quiz_cfg_get("max_total_questions", 20))
        # Use the answered ratio as a soft confidence proxy. The agent's
        # `decide_next_step` will run again before the *next* question, so
        # a wrong-but-plausible band here just shifts the user's perceived
        # progress by one question.
        confidence_proxy = min(1.0, (answered / max_total) if max_total else 0.0)
        cleaned = pick_progress_phrase(
            confidence=confidence_proxy,
            answered=answered,
            max_total=max_total,
        )

        logger.info("tool.generate_next_question.ok")
        return QuizQuestion(
            question_text=qt, options=opts, progress_phrase=cleaned, dimension=dim
        )
    except Exception as e:
        logger.error("tool.generate_next_question.fail", error=str(e), exc_info=True)
        return QuizQuestion(
            question_text="(Unable to generate the next question right now)",
            options=[{"text": "Continue"}, {"text": "Skip"}],
            progress_phrase=pick_progress_phrase(
                confidence=0.0,
                answered=len(quiz_history or []),
                max_total=int(_quiz_cfg_get("max_total_questions", 20)),
            ),
        )


@tool(description="Decide whether to ask one more question or finish now based on quiz history.")
async def decide_next_step(
    quiz_history: list[Any],          # Changed from List[Dict[str, Any]]
    character_profiles: list[Any],    # Changed from List[Dict[str, Any]]
    synopsis: Any,                    # Changed from Dict[str, Any]
    analysis: dict[str, Any] | None = None,
    trace_id: str | None = None,
    session_id: str | None = None,
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

    # Topic-aware effective floor: rigorous instruments (DISC, MBTI, …) ask more
    # questions before an early finish. We surface the SAME floor the graph gate
    # enforces so the prompt's "may finish early only if total >= N" line is
    # accurate per-topic instead of always quoting the global floor.
    eff_min, eff_max = _effective_depth_floor_cap(
        analysis.get("normalized_category") or inferred_category
    )

    prompt = prompt_manager.get_prompt("decision_maker")
    messages = prompt.invoke(
        {
            "quiz_history": [_to_dict(i) for i in (quiz_history or [])],
            "character_profiles": [_to_dict(c) for c in (character_profiles or [])],
            "synopsis": _to_dict(synopsis) if synopsis is not None else {},
            "min_questions_before_finish": eff_min,
            "confidence_threshold": _quiz_cfg_get("early_finish_confidence", 0.9),
            "max_total_questions": eff_max,
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


# ---------------------------------------------------------------------------
# Final-profile quality gate (AC-QUALITY-FINALPROFILE-1, -2)
# ---------------------------------------------------------------------------

# The final reading is the single biggest UX moment in the entire quiz.
# We enforce these floors so the user is never handed a thin one-liner.
MIN_FINAL_PARAGRAPHS: int = 3
MIN_FINAL_DESCRIPTION_CHARS: int = 400

# Match a paragraph break: blank line OR run of newlines, optionally with
# whitespace. Splitting on this and dropping empties gives us a count.
_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n+")


def _count_paragraphs(text: str | None) -> int:
    """Count non-empty paragraph blocks in `text`.

    A paragraph is anything separated by one or more blank lines. We
    additionally tolerate a single hard newline (so authors who write
    `Para 1\nPara 2` instead of `Para 1\n\nPara 2` aren't penalised when
    the lines are clearly distinct sentences). We don't try to be clever
    here — false positives are fine because the LLM almost always uses
    explicit blank-line breaks when prompted, and the worst case is one
    extra retry.
    """
    if not text:
        return 0
    blocks = [b.strip() for b in _PARAGRAPH_SPLIT_RE.split(text) if b and b.strip()]
    return len(blocks)


def _is_final_profile_substantive(out: FinalResult | None) -> bool:
    """Pass criteria for a publishable final reading: enough paragraphs AND length.

    Both are required: a single 600-character wall-of-text fails because
    the UI renders it as one block; three 80-character paragraphs also
    fail because the content is too thin to feel personalised.
    """
    if out is None:
        return False
    desc = (out.description or "").strip()
    if len(desc) < MIN_FINAL_DESCRIPTION_CHARS:
        return False
    if _count_paragraphs(desc) < MIN_FINAL_PARAGRAPHS:
        return False
    return True


async def _ensure_multiparagraph_profile(
    *,
    out: FinalResult,
    base_messages: list[Any],
    winning_character: dict[str, Any],
    trace_id: str | None,
    session_id: str | None,
) -> FinalResult:
    """Re-prompt ONCE if the first reading came back too thin.

    We avoid an unbounded retry loop: a second failure falls through to
    the caller's existing graceful fallback (which composes a multi-paragraph
    profile from the winning character's existing `profile_text`).
    """
    if _is_final_profile_substantive(out):
        return out

    logger.warning(
        "tool.write_final_user_profile.thin_first_pass",
        paragraph_count=_count_paragraphs(out.description),
        char_count=len((out.description or "").strip()),
        winner=winning_character.get("name"),
    )

    # Add a stronger user-side instruction asking explicitly for ≥3
    # paragraphs separated by blank lines and a higher word floor.
    booster = HumanMessage(
        content=(
            "Your previous reply was too short or had too few paragraphs. "
            f"Rewrite the description so it contains at least {MIN_FINAL_PARAGRAPHS} "
            "substantial paragraphs (separated by a single blank line) and "
            f"totals at least {MIN_FINAL_DESCRIPTION_CHARS} characters. "
            "Reference at least one specific answer the user gave in the quiz. "
            "Return only the JSON object — no commentary, no code fences."
        )
    )
    boosted_messages = list(base_messages) + [booster]

    try:
        retried: FinalResult = await invoke_structured(
            tool_name="final_profile_writer",
            messages=boosted_messages,
            response_model=FinalResult,
            explicit_schema=jsonschema_for("final_profile_writer"),
            trace_id=trace_id,
            session_id=session_id,
        )
    except Exception as e:
        logger.warning(
            "tool.write_final_user_profile.retry_failed",
            error=str(e),
        )
        # Re-raise so the outer `except Exception` falls into the
        # graceful fallback path (which produces a substantial profile
        # from the winning character data).
        raise

    if _is_final_profile_substantive(retried):
        logger.info(
            "tool.write_final_user_profile.retry_ok",
            paragraph_count=_count_paragraphs(retried.description),
            char_count=len((retried.description or "").strip()),
        )
        return retried

    # Second pass still thin — keep the longer of the two so the caller's
    # post-processing has the best material to work with. The caller does
    # NOT call the fallback path on this branch (no exception was raised);
    # the post-processed result will still be the most substantive of the
    # two LLM attempts, and downstream behaviour is unchanged.
    logger.warning(
        "tool.write_final_user_profile.retry_still_thin",
        first_chars=len((out.description or "").strip()),
        retry_chars=len((retried.description or "").strip()),
    )
    if len((retried.description or "").strip()) > len((out.description or "").strip()):
        return retried
    return out


@tool(description="Write the final, personalized quiz result for the user.")
async def write_final_user_profile(
    winning_character: dict[str, Any],
    quiz_history: list[dict[str, Any]],
    trace_id: str | None = None,
    session_id: str | None = None,
    # Graph passes these explicitly; we fall back to character fields, then defaults.
    category: str | None = None,
    outcome_kind: str | None = None,
    creativity_mode: str | None = None,
) -> FinalResult:
    logger.info("tool.write_final_user_profile.start", character=winning_character.get("name"))

    _category = (category or winning_character.get("category") or "").strip()
    _outcome_kind = (outcome_kind or "types").strip()
    _creativity_mode = (creativity_mode or "balanced").strip()

    prompt = prompt_manager.get_prompt("final_profile_writer")
    base_messages = prompt.invoke(
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
            messages=base_messages,
            response_model=FinalResult,
            explicit_schema=jsonschema_for("final_profile_writer"),
            trace_id=trace_id,
            session_id=session_id,
        )

        # Quality gate (AC-QUALITY-FINALPROFILE-1):
        # The user's takeaway from the entire quiz is this one block of
        # text — a one-paragraph "you are bold and curious" reading is the
        # single biggest disappointment vector. The prompt asks for 3–5
        # paragraphs; we re-prompt ONCE with stronger guidance if the
        # model returned fewer, then fall through to the graceful fallback
        # path so we never surface a thin reading to the user.
        out = await _ensure_multiparagraph_profile(
            out=out,
            base_messages=base_messages,
            winning_character=winning_character,
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

        logger.info(
            "tool.write_final_user_profile.ok",
            paragraph_count=_count_paragraphs(out.description),
            char_count=len(out.description),
        )
        return out

    except Exception as e:
        logger.error("tool.write_final_user_profile.fail", error=str(e), exc_info=True)
        # Graceful, schema-valid fallback. We try to compose something
        # substantial from the winning character's existing profile_text
        # rather than returning a single sentence — a one-liner here looks
        # broken to the user even though the request technically succeeded.
        name = winning_character.get("name") or "Your Best Self"
        fallback_title = f"You are {name}!"
        profile_text = (winning_character.get("profile_text") or "").strip()
        short_desc = (winning_character.get("short_description") or "").strip()
        parts: list[str] = []
        opener = (
            f"Your answers in this quiz consistently aligned with {name}. "
            "That match isn't accidental \u2014 the choices you made point at a coherent way of moving through the world."
        )
        parts.append(opener)
        if profile_text:
            parts.append(profile_text)
        elif short_desc:
            parts.append(short_desc)
        else:
            # No character bio available — synthesise a middle paragraph so
            # we still ship at least three blocks of substance to the user
            # (AC-QUALITY-FINALPROFILE-2).
            parts.append(
                f"People who land on {name} tend to share a recognisable pattern: "
                "they value depth over noise, they keep a steady hand under pressure, "
                "and they let their curiosity pull them toward problems most people walk past. "
                "Your responses leaned that direction throughout the quiz, which is why this profile fits."
            )
        parts.append(
            "Lean into what makes this profile yours, stay curious about its blind spots, "
            "and you'll keep growing into an even sharper version of it."
        )
        description = "\n\n".join(p for p in parts if p)
        return FinalResult(
            title=fallback_title,
            description=description,
            image_url=winning_character.get("image_url"),
        )


# ---------------------------------------------------------------------------
# Blended-profile writer (DISC pilot etc.)
# ---------------------------------------------------------------------------

# Minimum narrative floors mirror the single-character reading so a blended
# result is never thinner than a single-character one.
MIN_BLEND_NARRATIVE_PARAGRAPHS: int = MIN_FINAL_PARAGRAPHS
MIN_BLEND_NARRATIVE_CHARS: int = MIN_FINAL_DESCRIPTION_CHARS


def _clamp_emphasis(value: Any) -> int:
    """Coerce an arbitrary emphasis value to an int in [0, 100]."""
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return max(0, min(100, v))


def _align_dimensions_to_palette(
    raw_dimensions: list[dict[str, Any]], palette: list[str]
) -> list[BlendedDimension]:
    """Snap LLM-returned dimensions onto the canonical palette.

    The canonical names are authoritative: the model can never add, drop, or
    rename a member. We index the model output case-blind by name, then emit
    exactly one ``BlendedDimension`` per palette entry (in palette order),
    filling a missing/unmatched dimension with a neutral default so the result
    is always palette-consistent (the same contract the persist-time gate uses).
    """
    by_name: dict[str, dict[str, Any]] = {}
    for d in raw_dimensions or []:
        if not isinstance(d, dict):
            continue
        nm = str(d.get("name") or "").strip().casefold()
        if nm and nm not in by_name:
            by_name[nm] = d

    out: list[BlendedDimension] = []
    for name in palette:
        match = by_name.get(name.strip().casefold())
        if match is not None:
            blurb = str(match.get("blurb") or "").strip() or f"{name} shows up in how you answered."
            out.append(
                BlendedDimension(
                    name=name,
                    emphasis=_clamp_emphasis(match.get("emphasis")),
                    blurb=blurb,
                )
            )
        else:
            out.append(
                BlendedDimension(
                    name=name,
                    emphasis=0,
                    blurb=f"{name} played a smaller role in your answers.",
                )
            )
    return out


def _resolve_blend_primary_secondary(
    dimensions: list[BlendedDimension],
    llm_primary: str | None,
    llm_secondary: str | None,
) -> tuple[str, str | None]:
    """Pick the primary/secondary blend, trusting emphasis order over LLM labels.

    The canonical emphasis ranking is the source of truth (so primary/secondary
    can never name a dimension outside the palette). We honour the LLM's primary
    only when it actually matches a palette member; otherwise the highest
    emphasis wins. Secondary is the next-highest, dropped when the profile is
    effectively flat (no positive secondary emphasis).
    """
    if not dimensions:
        return ("", None)
    ranked = sorted(dimensions, key=lambda d: d.emphasis, reverse=True)
    names = {d.name.casefold(): d.name for d in dimensions}

    primary = ranked[0].name
    if llm_primary and llm_primary.strip().casefold() in names:
        primary = names[llm_primary.strip().casefold()]

    secondary: str | None = None
    for d in ranked:
        if d.name == primary:
            continue
        if d.emphasis > 0:
            secondary = d.name
            break
    # Honour an explicit, palette-valid LLM secondary when present & distinct.
    if llm_secondary and llm_secondary.strip().casefold() in names:
        cand = names[llm_secondary.strip().casefold()]
        if cand != primary:
            secondary = cand
    return (primary, secondary)


def _blend_label(primary: str, secondary: str | None) -> str:
    """Compact blend label, e.g. "D/C" from primary/secondary first letters."""
    p = (primary or "").strip()
    if not p:
        return ""
    if secondary and secondary.strip():
        return f"{p[0].upper()}/{secondary.strip()[0].upper()}"
    return p[0].upper()


@tool(description="Write the final result as a BLENDED PROFILE across canonical dimensions (e.g. DISC).")
async def write_blended_profile(
    winning_character: dict[str, Any],
    quiz_history: list[dict[str, Any]],
    dimensions: list[str],
    trace_id: str | None = None,
    session_id: str | None = None,
    category: str | None = None,
    creativity_mode: str | None = None,
) -> FinalResult:
    """Generate a blended-profile ``FinalResult`` for a pilot blended topic.

    ``dimensions`` is the canonical palette (from ``canonical_for``). The output
    is a ``FinalResult`` with ``result_kind="blended_profile"`` and a populated
    ``profile`` (per-dimension emphasis/blurb + primary/secondary + narrative).
    The single-character ``title``/``description`` fields stay populated too so
    any consumer that ignores ``profile`` still renders a coherent reading.
    """
    palette = [str(d).strip() for d in (dimensions or []) if str(d).strip()]
    logger.info(
        "tool.write_blended_profile.start",
        category=category,
        dimension_count=len(palette),
    )

    _category = (category or "").strip()
    _creativity_mode = (creativity_mode or "balanced").strip()

    if not palette:
        # No palette → we cannot build a blend; fall back to single-character.
        logger.warning("tool.write_blended_profile.no_palette", category=_category)
        return await write_final_user_profile.ainvoke(
            {
                "winning_character": winning_character,
                "quiz_history": quiz_history,
                "trace_id": trace_id,
                "session_id": session_id,
                "category": _category,
                "creativity_mode": _creativity_mode,
            }
        )

    prompt = prompt_manager.get_prompt("blended_profile_writer")
    base_messages = prompt.invoke(
        {
            "category": _category,
            "dimension_names": ", ".join(palette),
            "quiz_history": quiz_history,
            "creativity_mode": _creativity_mode,
        }
    ).messages

    try:
        # The writer returns a loose object; we validate/repair against the
        # palette ourselves, so request a plain dict-shaped model.
        raw: dict[str, Any] = await invoke_structured(
            tool_name="blended_profile_writer",
            messages=base_messages,
            response_model=dict,
            explicit_schema=jsonschema_for("blended_profile_writer"),
            trace_id=trace_id,
            session_id=session_id,
        )
        if not isinstance(raw, dict):
            raw = dict(getattr(raw, "__dict__", {}) or {})

        aligned = _align_dimensions_to_palette(
            list(raw.get("dimensions") or []), palette
        )
        primary, secondary = _resolve_blend_primary_secondary(
            aligned, raw.get("primary"), raw.get("secondary")
        )
        narrative = str(raw.get("narrative") or "").strip()

        # Quality gate: the blend narrative is the user's takeaway — never ship
        # a thin one. If too short/flat, synthesise a substantive fallback from
        # the dimensions rather than re-billing the LLM (keeps the pilot cheap).
        if (
            len(narrative) < MIN_BLEND_NARRATIVE_CHARS
            or _count_paragraphs(narrative) < MIN_BLEND_NARRATIVE_PARAGRAPHS
        ):
            logger.warning(
                "tool.write_blended_profile.thin_narrative",
                chars=len(narrative),
                paragraphs=_count_paragraphs(narrative),
            )
            narrative = _compose_blend_narrative_fallback(
                aligned, primary, secondary, _category
            )

        label = _blend_label(primary, secondary)
        title = str(raw.get("title") or "").strip() or (
            f"You're a {label} blend" if label else "Your blended profile"
        )

        profile = BlendedProfile(
            dimensions=aligned,
            primary=primary,
            secondary=secondary,
            narrative=narrative,
        )
        result = FinalResult(
            title=title,
            description=narrative,
            image_url=(winning_character or {}).get("image_url"),
            result_kind="blended_profile",
            profile=profile,
        )
        logger.info(
            "tool.write_blended_profile.ok",
            primary=primary,
            secondary=secondary,
            narrative_chars=len(narrative),
        )
        return result

    except Exception as e:
        logger.error("tool.write_blended_profile.fail", error=str(e), exc_info=True)
        # Deterministic, schema-valid fallback: a flat-but-valid blend across the
        # palette plus a composed narrative so the user still gets a real
        # blended profile rather than an error card.
        aligned = [
            BlendedDimension(
                name=name,
                emphasis=50,
                blurb=f"{name} is part of how you showed up across the quiz.",
            )
            for name in palette
        ]
        primary = palette[0]
        secondary = palette[1] if len(palette) > 1 else None
        narrative = _compose_blend_narrative_fallback(
            aligned, primary, secondary, _category
        )
        label = _blend_label(primary, secondary)
        return FinalResult(
            title=f"You're a {label} blend" if label else "Your blended profile",
            description=narrative,
            image_url=(winning_character or {}).get("image_url"),
            result_kind="blended_profile",
            profile=BlendedProfile(
                dimensions=aligned,
                primary=primary,
                secondary=secondary,
                narrative=narrative,
            ),
        )


def _compose_blend_narrative_fallback(
    dimensions: list[BlendedDimension],
    primary: str,
    secondary: str | None,
    category: str,
) -> str:
    """Compose a substantive (≥3 paragraph) blend narrative without the LLM.

    Used when the model returns a thin narrative or errors out. It reads the
    aligned dimensions so the prose still reflects the computed emphasis blend
    rather than a generic template.
    """
    framework = category or "this framework"
    primary_dim = next((d for d in dimensions if d.name == primary), None)
    secondary_dim = next((d for d in dimensions if d.name == secondary), None) if secondary else None

    opener_blend = f"{primary} with a strong assist from {secondary}" if secondary else f"a {primary}-led profile"
    para1 = (
        f"Your answers across this {framework} quiz don't land on a single label — they form a blend, "
        f"led by {opener_blend}. That combination is the real story: it's how these styles reinforce "
        "and balance each other in you, not any one of them on its own."
    )

    middle_bits: list[str] = []
    if primary_dim is not None:
        middle_bits.append(f"{primary_dim.name}: {primary_dim.blurb}")
    if secondary_dim is not None:
        middle_bits.append(f"{secondary_dim.name}: {secondary_dim.blurb}")
    others = [d for d in dimensions if d.name not in {primary, secondary}]
    if others:
        middle_bits.append(
            "The rest of the picture — "
            + "; ".join(f"{d.name.lower()} at {d.emphasis}" for d in others)
            + " — rounds out the blend and shows where you flex less often."
        )
    para2 = " ".join(middle_bits) or (
        "Each dimension contributes a distinct flavour, and your blend leans on the strongest two while "
        "keeping the others in reserve."
    )

    para3 = (
        f"Day to day, expect your {primary} side to set the pace"
        + (f", with {secondary} shaping how you do it" if secondary else "")
        + ". Lean into that combination where it serves you, and watch for the moments when leaning too hard "
        "on your top style crowds out the balance the rest of your profile can provide."
    )
    return "\n\n".join([para1, para2, para3])
