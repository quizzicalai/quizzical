# backend/app/agent/graph.py
"""
Main Agent Graph (synopsis/characters first → gated questions)

This LangGraph builds a quiz in two phases:

1) User-facing preparation
   - bootstrap → deterministic synopsis + archetype list (via planning_tools)
   - generate_characters → detailed character profiles (BATCH-FIRST with safe fallback)
   These run during /quiz/start. The request returns once synopsis (and
   typically characters) are ready.

2) Gated question generation
   - Only when the client calls /quiz/proceed, the API flips a state flag
     `ready_for_questions=True`. On the next graph run, a router sends flow
     to `generate_baseline_questions`, then to the sink.

Design notes:
- Nodes are idempotent: re-running after END will not redo work that exists.
- Removes legacy planner/tools loop and any `.to_dict()` tool usage.
- Uses async Redis checkpointer per langgraph-checkpoint-redis guidance.
- Aligns with planning_tools.py: normalize_topic/plan_quiz/generate_character_list
  wrappers supply steering signals without changing state keys.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Dict, List, Optional, Literal

import structlog
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.graph import END, StateGraph

# Import canonical models from agent.state (re-exported from schemas)
from app.agent.state import GraphState, Synopsis, CharacterProfile, QuizQuestion
from app.agent.canonical_sets import canonical_for, count_hint_for
from app.agent.tools.intent_classification import analyze_topic
from app.models.api import FinalResult

# Planning & content tools (wrappers; keep names for compatibility)
from app.agent.tools.planning_tools import (
    InitialPlan,
    normalize_topic as tool_normalize_topic,  # (kept import for compat; no longer used in _bootstrap_node)
    plan_quiz as tool_plan_quiz,
    generate_character_list as tool_generate_character_list,
)
from app.agent.tools.content_creation_tools import (
    generate_baseline_questions as tool_generate_baseline_questions,
    generate_next_question as tool_generate_next_question,
    decide_next_step as tool_decide_next_step,
    write_final_user_profile as tool_write_final_user_profile,
    draft_character_profile as tool_draft_character_profile,
)

# Soft-import the batch character tool; gracefully fall back if unavailable
try:  # pragma: no cover - import guard
    from app.agent.tools.content_creation_tools import (  # type: ignore
        draft_character_profiles as tool_draft_character_profiles,  # batch mode
    )
except Exception:  # pragma: no cover - absent in some deployments
    tool_draft_character_profiles = None  # type: ignore[assignment]

from app.core.config import settings as _base_settings
from app.services.llm_service import llm_service, coerce_json
from app.agent.schemas import NextStepDecision, SCHEMA_REGISTRY as _SCHEMA_REGISTRY

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _SettingsProxy:
    """ Proxy to allow dynamic overrides in tests via attribute setting."""
    def __init__(self, base):
        object.__setattr__(self, "_base", base)
        object.__setattr__(self, "_overrides", {})

    def __getattr__(self, name):
        ov = object.__getattribute__(self, "_overrides")
        if name in ov:
            return ov[name]
        return getattr(object.__getattribute__(self, "_base"), name)

    def __setattr__(self, name, value):
        object.__getattribute__(self, "_overrides")[name] = value

# Export proxy so tests target graph_mod.settings
settings = _SettingsProxy(_base_settings)


def schema_for(tool_name: str):
    """Convenience accessor to look up the default response model for a tool.

    Re-exported here so tests and callers can use app.agent.graph.schema_for(...)
    """
    return _SCHEMA_REGISTRY.get(tool_name)

def _env_name() -> str:
    try:
        return (settings.app.environment or "local").lower()
    except Exception:
        return "local"


def _safe_len(x):
    try:
        return len(x)  # type: ignore[arg-type]
    except Exception:
        return None


def _to_plain(obj: Any) -> Any:
    """Return a plain Python object (dict/primitive) for Pydantic-like inputs; pass dicts through."""
    if hasattr(obj, "model_dump"):
        try:
            return obj.model_dump()
        except Exception:
            pass
    return obj

def _safe_getattr(obj: Any, attr: str, default: Any = None) -> Any:
    """Safely access attributes on models or keys on dicts."""
    if hasattr(obj, attr):
        try:
            return getattr(obj, attr)
        except Exception:
            return default
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return default


def _validate_synopsis_payload(payload: Any) -> Synopsis:
    """
    Normalize any raw LLM payload into a valid Synopsis immediately.
    Prevents leaking dicts/strings into state/Redis.
    """
    if isinstance(payload, Synopsis):
        return payload
    data = coerce_json(payload)
    return Synopsis.model_validate(data)


def _validate_character_payload(payload: Any) -> CharacterProfile:
    """
    Normalize any raw LLM payload into a valid CharacterProfile immediately.
    """
    if isinstance(payload, CharacterProfile):
        return payload
    data = coerce_json(payload)
    return CharacterProfile.model_validate(data)


def _coerce_question_to_state(obj: Any) -> QuizQuestion:
    """
    Accepts a QuizQuestion, QuestionOut, or dict/loose object and produces a
    **state-shaped** QuizQuestion (question_text + options: List[Dict[str, str]]).
    This avoids cache validation failures and keeps /quiz/status logic predictable.
    """
    # Already correct type
    if isinstance(obj, QuizQuestion):
        return obj

    # Start from a dict perspective
    d = _to_plain(obj) if not isinstance(obj, dict) else obj
    if not isinstance(d, dict):
        # Fallback: treat as a bare text question with no options
        return QuizQuestion.model_validate({"question_text": str(d), "options": []})

    text = d.get("question_text") or d.get("text") or ""
    raw_options = d.get("options") or []
    options: List[Dict[str, str]] = []

    for o in raw_options:
        if isinstance(o, dict):
            # normalize common aliases
            t = o.get("text") or o.get("label") or str(o)
            img = o.get("image_url") or o.get("imageUrl") or None
            opt = {"text": str(t)}
            if img:
                opt["image_url"] = str(img)
            options.append(opt)
        elif hasattr(o, "model_dump"):
            od = _to_plain(o) or {}
            t = od.get("text") or od.get("label") or str(od)
            img = od.get("image_url") or od.get("imageUrl") or None
            opt = {"text": str(t)}
            if img:
                opt["image_url"] = str(img)
            options.append(opt)

        else:
            options.append({"text": str(o)})

    # Validate to ensure strict state shape
    return QuizQuestion.model_validate({"question_text": str(text), "options": options})

def _ensure_min_options(options: List[Dict[str, Any]], minimum: int = 2) -> List[Dict[str, Any]]:
    """
    Ensure each question has at least `minimum` options, returning **plain dicts**.
    - Filters out malformed entries (missing/blank text).
    - Does not persist nulls; `image_url` is omitted unless truthy.
    - Pads deterministically with generic choices.
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

def _dedupe_options_by_text(options: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Case/space-insensitive dedupe by 'text', preserving order and upgrading image_url if a later dup has one."""
    seen: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    def _key(s: str) -> str:
        return " ".join(s.split()).casefold()
    for o in options or []:
        t = str(o.get("text") or "").strip()
        if not t:
            continue
        k = _key(t)
        if k not in seen:
            item: Dict[str, Any] = {"text": t}
            if isinstance(o.get("image_url"), str) and o["image_url"].strip():
                item["image_url"] = o["image_url"].strip()
            seen[k] = item
            order.append(k)
        else:
            if not seen[k].get("image_url") and isinstance(o.get("image_url"), str) and o["image_url"].strip():
                seen[k]["image_url"] = o["image_url"].strip()
    return [seen[k] for k in order]

# ---------------------------------------------------------------------------
# Node: bootstrap (deterministic synopsis + archetypes)
# ---------------------------------------------------------------------------


async def _bootstrap_node(state: GraphState) -> dict:
    """
    Create/ensure a synopsis and a target list of character archetypes.
    Idempotent: If a synopsis already exists, returns no-op.

    UPDATED (collapse to a single LLM call for planning):
    - Skip tool_normalize_topic; instead use local analyze_topic(category).
    - Call ONLY plan_quiz initially.
    - Build Synopsis directly from plan (ensure "Quiz: " prefix on title).
    - Use planner-provided ideal_archetypes unless empty/outside [min_chars, max_chars].
      If outside, call tool_generate_character_list ONCE to repair (pass plan synopsis).
      If still short or long, clamp/accept (no second repair attempt).
    """
    if state.get("synopsis"):
        logger.debug("bootstrap_node.noop", reason="synopsis_already_present")
        return {}

    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    category = state.get("category") or (
        state["messages"][0].content if state.get("messages") else ""
    )

    logger.info(
        "bootstrap_node.start",
        session_id=session_id,
        trace_id=trace_id,
        category_preview=str(category)[:120],
        env=_env_name(),
    )

    # Local helper mirrors content_creation_tools._ensure_quiz_prefix, scoped to this node
    def _ensure_quiz_prefix_local(title: str) -> str:
        import re as _re
        t = (title or "").strip()
        if not t:
            return "Quiz: Untitled"
        t = _re.sub(r"(?i)^quiz\s*[:\-–—]\s*", "", t).strip()
        return f"Quiz: {t}"

    # ---- Analyze topic locally (no LLM) ----
    try:
        a = analyze_topic(category)
        category = a.get("normalized_category") or category
        okind = a.get("outcome_kind") or "types"
        cmode = a.get("creativity_mode") or "balanced"
        names_only = bool(a.get("names_only"))
        intent = a.get("intent") or "identify"
        domain = a.get("domain") or ""
    except Exception:
        okind, cmode = "types", "balanced"
        intent = "identify"
        names_only = False
        domain = ""

    # ---- Single LLM call: plan the quiz ----
    t0 = time.perf_counter()
    plan: InitialPlan
    try:
        plan = await tool_plan_quiz.ainvoke({
            "category": category,
            "outcome_kind": okind,
            "creativity_mode": cmode,
            "intent": intent,
            "names_only": names_only,
            "trace_id": trace_id,
            "session_id": str(session_id),
        })
    except Exception as e:
        logger.warning("bootstrap_node.plan_quiz.fail", error=str(e), exc_info=True)
        # Fallback to bare minimum plan
        # Fallback to a usable plan (non-empty synopsis & archetypes)
        plan = InitialPlan(
            title=f"What {category} Are You?",
            synopsis=f"A fun quiz about {category}.",
            ideal_archetypes=["The Analyst", "The Dreamer", "The Realist"],
        )
    dt_ms = round((time.perf_counter() - t0) * 1000, 1)
    logger.info(
        "bootstrap_node.plan_quiz.ok",
        session_id=session_id,
        trace_id=trace_id,
        duration_ms=dt_ms,
    )

    # ---- Build synopsis from the plan directly ----
    plan_title = getattr(plan, "title", None) or f"What {category} Are You?"
    plan_synopsis = getattr(plan, "synopsis", None) or ""
    synopsis_obj = Synopsis(
        title=_ensure_quiz_prefix_local(plan_title),
        summary=plan_synopsis,
    )
    if names_only and synopsis_obj.summary:
        synopsis_obj.summary += " You'll answer a few questions and we’ll match you to a well-known name."

    # ---- Prefer canonical sets when present; otherwise planner-provided ----
    canon = canonical_for(category)
    if canon:
        raw_archetypes = list(canon)
        # Hint to downstream (helps prompts/tools choose the right target count)
        plan.ideal_count_hint = count_hint_for(category) or len(raw_archetypes)
    else:
        raw_archetypes = getattr(plan, "ideal_archetypes", None) or []

    archetypes = [n.strip() for n in raw_archetypes if isinstance(n, str) and n.strip()]

    min_chars = getattr(getattr(settings, "quiz", object()), "min_characters", 4)
    max_chars = getattr(getattr(settings, "quiz", object()), "max_characters", 32)

    needs_repair = (not archetypes) or (len(archetypes) < min_chars) or (len(archetypes) > max_chars)
    if not needs_repair and names_only:
        def _looks_like_name(s: str) -> bool:
            w = str(s).strip().split()
            return any(tok[:1].isupper() for tok in w[:2]) or ("-" in s) or ("." in s)
        if not all(_looks_like_name(n) for n in archetypes):
            needs_repair = True

    if needs_repair:
        repaired_names: List[str] = []
        try:
            repaired = await tool_generate_character_list.ainvoke({
                "category": category,
                "synopsis": synopsis_obj.summary,
                "analysis": a,
                "trace_id": trace_id,
                "session_id": str(session_id),
            })
            if isinstance(repaired, list):
                repaired_names = repaired
            elif hasattr(repaired, "archetypes"):
                repaired_names = list(getattr(repaired, "archetypes") or [])
        except Exception as e:
            logger.debug("bootstrap_node.archetypes.repair.skipped", reason=str(e))
        if repaired_names:
            archetypes = [n.strip() for n in repaired_names if isinstance(n, str) and n.strip()]

    # Clamp to max; if still short (< min), accept as-is (no second attempt)
    if max_chars and isinstance(max_chars, int):
        archetypes = archetypes[:max_chars]

    # Final guard: never leave this node with zero archetypes
    if not archetypes:
        archetypes = ["The Analyst", "The Dreamer", "The Realist"]

    plan_summary = f"Planned '{category}'. Synopsis ready. Target characters: {archetypes}"

    # Only validated models are written to state:
    return {
        "messages": [AIMessage(content=plan_summary)],
        "category": category,
        "synopsis": synopsis_obj,  # validated Synopsis
        "ideal_archetypes": archetypes,
        "topic_analysis": a,  # raw analysis dict
        "outcome_kind": okind,
        "creativity_mode": cmode,
        "is_error": False,
        "error_message": None,
        "error_count": 0,
    }


# ---------------------------------------------------------------------------
# Node: generate_characters (BATCH-FIRST with safe fallback)
# ---------------------------------------------------------------------------


async def _generate_characters_node(state: GraphState) -> dict:
    """
    Create detailed character profiles for each archetype in an order-preserving, batch-first flow.
    Idempotent: If characters already exist, returns no-op.

    Updates:
    - Prefer a single batch tool call when available (lower latency, fewer tokens).
    - Gracefully fall back to per-item async generation with retries and jittered backoff.
    - Preserve requested order; perform a deterministic "name lock" (returned profile name matches requested label).
    - Do not write an empty list; omit key if none succeeded.
    """
    if state.get("generated_characters"):
        logger.debug("characters_node.noop", reason="characters_already_present")
        return {}

    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    category = state.get("category")
    analysis = state.get("topic_analysis") or {}
    archetypes: List[str] = state.get("ideal_archetypes") or []

    if not archetypes:
        logger.warning(
            "characters_node.no_archetypes", session_id=session_id, trace_id=trace_id
        )
        return {
            "messages": [AIMessage(content="No archetypes to generate characters for.")],
        }

    default_concurrency = min(4, max(1, len(archetypes)))
    concurrency = (
        getattr(getattr(settings, "quiz", object()), "character_concurrency", default_concurrency)
        or default_concurrency
    )
    per_call_timeout_s = getattr(getattr(settings, "llm", object()), "per_call_timeout_s", 30)
    max_retries = int(getattr(getattr(settings, "agent", object()), "max_retries", 3))

    logger.info(
        "characters_node.start",
        session_id=session_id,
        trace_id=trace_id,
        target_count=len(archetypes),
        category=category,
        concurrency=concurrency,
        timeout_s=per_call_timeout_s,
    )

    # ---- Helper: name-locked validation ----
    def _lock_name(prof: CharacterProfile, name: str) -> CharacterProfile:
        try:
            if (prof.name or "").strip().casefold() != (name or "").strip().casefold():
                return CharacterProfile(
                    name=name,
                    short_description=prof.short_description,
                    profile_text=prof.profile_text,
                    image_url=getattr(prof, "image_url", None),
                )
            return prof
        except Exception:
            return CharacterProfile(name=name, short_description="", profile_text="")

    # ---- Try batch tool first (if present) ----
    results_map: Dict[str, Optional[CharacterProfile]] = {n: None for n in archetypes}
    if tool_draft_character_profiles is not None and len(archetypes) > 1:
        try:
            t0 = time.perf_counter()
            payload = {
                "category": category,
                "character_names": archetypes,
                "analysis": analysis,
                "trace_id": trace_id,
                "session_id": str(session_id),
            }
            raw_batch = await asyncio.wait_for(
                tool_draft_character_profiles.ainvoke(payload),  # type: ignore[attr-defined]
                timeout=per_call_timeout_s,
            )

            # Accept list or dict outputs; normalize to name->profile mapping
            if isinstance(raw_batch, dict):
                pairs = raw_batch.items()
            else:
                # best-effort: if a list, align by index or look for 'name' keys
                pairs = []
                seq = list(raw_batch or [])
                for i, n in enumerate(archetypes):
                    item = seq[i] if i < len(seq) else None
                    pairs.append((n, item))

            for req_name, raw in pairs:
                if raw is None:
                    continue
                try:
                    prof = _validate_character_payload(raw)
                    results_map[req_name] = _lock_name(prof, req_name)
                except Exception as e:
                    logger.debug("characters_node.batch.item_invalid", character=req_name, error=str(e))

            dt_ms = round((time.perf_counter() - t0) * 1000, 1)
            got = sum(1 for v in results_map.values() if v is not None)
            logger.debug(
                "characters_node.batch.ok",
                session_id=session_id,
                trace_id=trace_id,
                duration_ms=dt_ms,
                produced=got,
                requested=len(archetypes),
            )
        except Exception as e:
            logger.debug("characters_node.batch.fail", error=str(e))

    # ---- Fill any missing slots with per-item async calls ----
    sem = asyncio.Semaphore(concurrency)

    async def _attempt(name: str) -> Optional[CharacterProfile]:
        """One profile generation with timeout; returns CharacterProfile or None."""
        try:
            raw_payload = await asyncio.wait_for(
                tool_draft_character_profile.ainvoke({
                    "character_name": name,
                    "category": category,
                    "analysis": analysis,
                    "trace_id": trace_id,
                    "session_id": str(session_id),
                }),
                timeout=per_call_timeout_s,
            )
            prof = _validate_character_payload(raw_payload)
            return _lock_name(prof, name)
        except Exception as e:
            logger.debug("characters_node.attempt.fail", character=name, error=str(e))
            return None

    async def _one(name: str) -> None:
        t0 = time.perf_counter()
        for attempt in range(max_retries + 1):
            try:
                async with sem:
                    prof = await _attempt(name)
                if prof:
                    dt_ms = round((time.perf_counter() - t0) * 1000, 1)
                    logger.debug(
                        "characters_node.profile.ok",
                        session_id=session_id,
                        trace_id=trace_id,
                        character=name,
                        duration_ms=dt_ms,
                        attempt=attempt,
                    )
                    results_map[name] = prof
                    return
            except Exception:
                pass
            if attempt < max_retries:
                await asyncio.sleep(0.5 + 0.5 * attempt)
        logger.warning(
            "characters_node.profile.gave_up",
            session_id=session_id,
            trace_id=trace_id,
            character=name,
            retries=max_retries,
        )

    # Launch per-item fills only for missing names
    missing = [n for n, v in results_map.items() if v is None]
    if missing:
        tasks = [asyncio.create_task(_one(name)) for name in missing]
        await asyncio.gather(*tasks, return_exceptions=True)

    # Final ordered list; drop Nones
    characters: List[CharacterProfile] = [results_map[n] for n in archetypes if results_map.get(n) is not None]  # type: ignore[list-item]

    logger.info(
        "characters_node.done",
        session_id=session_id,
        trace_id=trace_id,
        generated_count=len(characters),
        requested=len(archetypes),
    )

    out: Dict[str, Any] = {
        "messages": [AIMessage(content=f"Generated {len(characters)} character profiles (batch-first).")],
        "is_error": False,
        "error_message": None,
    }
    if characters:
        out["generated_characters"] = characters  # validated CharacterProfile[]
    return out


# ---------------------------------------------------------------------------
# Node: generate_baseline_questions (gated)
# ---------------------------------------------------------------------------


async def _generate_baseline_questions_node(state: GraphState) -> dict:
    """
    Generate the initial set of baseline questions (single structured call).
    Idempotent:
      - If baseline questions already exist AND the baseline flag is set, returns no-op.
      - If questions exist but baseline flag is missing (legacy/migrated state), set the flag and count.
    PRECONDITION: Router ensures ready_for_questions=True before we get here.
    """
    # If questions already exist but baseline_ready is missing/False, set it once and return
    existing = state.get("generated_questions")
    if existing and not state.get("baseline_ready"):
        logger.debug("baseline_node.flag_backfill", reason="questions_exist_flag_missing")
        return {
            "baseline_ready": True,
            "baseline_count": len(existing),
            "messages": [AIMessage(content=f"Baseline questions already present: {len(existing)} (flag backfilled).")],
            "is_error": False,
            "error_message": None,
        }

    if state.get("baseline_ready"):
        logger.debug("baseline_node.noop", reason="baseline_already_ready")
        return {}

    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    category = state.get("category") or ""
    characters: List[CharacterProfile] = state.get("generated_characters") or []
    synopsis = state.get("synopsis")
    analysis = state.get("topic_analysis") or {}

    logger.info(
        "baseline_node.start",
        session_id=session_id,
        trace_id=trace_id,
        category=category,
        characters=len(characters),
    )

    # Optional “N baseline questions” control
    try:
        desired_n = int(getattr(getattr(settings, "quiz", object()), "baseline_questions_n", 0))
    except Exception:
        desired_n = 0

    t0 = time.perf_counter()
    questions_state: List[Dict[str, Any]] = []
    try:
        # v0: rely on typed inputs/outputs; dump Pydantic to plain dicts for the tool layer only
        characters_payload = [c.model_dump() if hasattr(c, "model_dump") else c for c in (characters or [])]
        synopsis_payload = synopsis.model_dump() if hasattr(synopsis, "model_dump") else {"title": "", "summary": ""}

        raw = await tool_generate_baseline_questions.ainvoke({
            "category": category,
            "character_profiles": characters_payload,
            "synopsis": synopsis_payload,
            "analysis": analysis,
            "trace_id": trace_id,
            "session_id": str(session_id),
            # If the tool supports it, great; if not, harmless.
            "num_questions": desired_n or None,
        })
        # v0: convert tool output (QuestionList | List[QuestionOut]) → List[QuizQuestion]
        def _to_quiz_question(obj) -> QuizQuestion:
            if isinstance(obj, QuizQuestion):
                return obj
            # expect QuestionOut shape
            text = getattr(obj, "question_text", None) or (obj.get("question_text") if isinstance(obj, dict) else "")
            opts = getattr(obj, "options", None) or (obj.get("options") if isinstance(obj, dict) else [])
            norm_opts: List[Dict[str, str]] = []
            for o in opts or []:
                if hasattr(o, "model_dump"):
                    o = o.model_dump()
                if isinstance(o, dict) and o.get("text"):
                    item = {"text": str(o["text"])}
                    if o.get("image_url"):
                        item["image_url"] = str(o["image_url"])
                    norm_opts.append(item)
            return QuizQuestion.model_validate({"question_text": str(text), "options": norm_opts})

        items = getattr(raw, "questions", None) if raw is not None else []
        if items is None and isinstance(raw, list):
            items = raw
        questions: List[QuizQuestion] = [_to_quiz_question(i) for i in (items or [])]
        if desired_n > 0:
            questions = questions[:desired_n]
        questions_state = [q.model_dump(mode="json", exclude_none=True) for q in questions]

    except Exception as e:
        logger.error("baseline_node.tool_fail", session_id=session_id, trace_id=trace_id, error=str(e), exc_info=True)
        questions_state = []

    dt_ms = round((time.perf_counter() - t0) * 1000, 1)
    logger.info(
        "baseline_node.done",
        session_id=session_id,
        trace_id=trace_id,
        duration_ms=dt_ms,
        produced=len(questions_state),
    )

    return {
        "messages": [AIMessage(content=f"Baseline questions ready: {len(questions_state)}")],
        "generated_questions": questions_state,  # **plain dicts, state shape**
        "baseline_count": len(questions_state),
        "baseline_ready": True,           # <-- explicit baseline flag, even if zero
        "is_error": False,
        "error_message": None,
    }


# ---------------------------------------------------------------------------
# Node: decide / finish / adaptive
# ---------------------------------------------------------------------------


async def _decide_or_finish_node(state: GraphState) -> dict:
    """Decide whether to finish or ask one more, robust to dict/model hydration."""
    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    synopsis = state.get("synopsis")
    characters = state.get("generated_characters") or []
    history = state.get("quiz_history") or []
    analysis = state.get("topic_analysis") or {}

    # Normalize payloads (dicts after Redis are fine)
    history_payload = [_to_plain(i) for i in (history or [])]
    characters_payload = [_to_plain(c) for c in (characters or [])]
    synopsis_payload = (
        synopsis.model_dump() if hasattr(synopsis, "model_dump")
        else (_to_plain(synopsis) or {"title": "", "summary": ""})
    )

    answered = len(history)
    baseline_count = int(state.get("baseline_count") or 0)
    max_q = int(getattr(getattr(settings, "quiz", object()), "max_total_questions", 20))
    min_early = int(getattr(getattr(settings, "quiz", object()), "min_questions_before_early_finish", 6))
    thresh = float(getattr(getattr(settings, "quiz", object()), "early_finish_confidence", 0.9))

    # Must answer all baseline before adaptive
    if answered < baseline_count:
        return {"should_finalize": False, "messages": [AIMessage(content="Awaiting baseline answers")]}

    # Default decision via tool (unless hard cap)
    action = "ASK_ONE_MORE_QUESTION"
    confidence = 0.0
    name = ""
    if answered >= max_q:
        action, confidence = "FINISH_NOW", 1.0
    else:
        try:
            decision = await tool_decide_next_step.ainvoke({
                "quiz_history": history_payload,
                "character_profiles": characters_payload,
                "synopsis": synopsis_payload,
                "analysis": analysis,
                "trace_id": trace_id,
                "session_id": str(session_id),
            })
            action = getattr(decision, "action", "ASK_ONE_MORE_QUESTION")
            confidence = float(getattr(decision, "confidence", 0.0) or 0.0)
            if confidence > 1.0:  # tolerate % scales
                confidence = min(1.0, confidence / 100.0)
            name = (getattr(decision, "winning_character_name", "") or "").strip()
        except Exception as e:
            logger.error("decide_node.tool_fail", error=str(e))

    # Apply deterministic business rules
    final_action = action
    if answered >= max_q:
        final_action = "FINISH_NOW"
    elif answered < min_early:
        final_action = "ASK_ONE_MORE_QUESTION"
    elif action == "FINISH_NOW" and confidence < thresh:
        final_action = "ASK_ONE_MORE_QUESTION"

    if final_action != "FINISH_NOW":
        return {"should_finalize": False, "current_confidence": confidence}

    # Pick a winner robustly
    winning = None
    if name:
        for c in characters:
            cname = _safe_getattr(c, "name", "")
            if cname and cname.strip().casefold() == name.casefold():
                winning = c
                break
    if not winning and characters:
        winning = characters[0]  # deterministic fallback
    if not winning:
        return {"should_finalize": False, "messages": [AIMessage(content="No winner available; ask one more.")]}

    try:
        category = state.get("category") or _safe_getattr(synopsis, "title", "").removeprefix("Quiz: ").strip()
        outcome_kind = state.get("outcome_kind") or "types"
        creativity_mode = state.get("creativity_mode") or "balanced"

        final = await tool_write_final_user_profile.ainvoke({
            "winning_character": _to_plain(winning),
            "quiz_history": history_payload,
            "trace_id": trace_id,
            "session_id": str(session_id),
            # pass through for writer to use
            "category": category,
            "outcome_kind": outcome_kind,
            "creativity_mode": creativity_mode,
        })
        return {"final_result": final, "should_finalize": True, "current_confidence": confidence}
    except Exception as e:
        logger.error("decide_node.final_result_fail", error=str(e))
        return {
            "final_result": FinalResult(
                title="Result Error",
                description="Failed to generate final profile.",
                image_url=None,
            ),
            "should_finalize": True,
            "current_confidence": confidence,
        }


async def _generate_adaptive_question_node(state: GraphState) -> dict:
    """
    Generate one adaptive question and append it to state in **state shape**.
    Fixes: preserve dict-based history after cache round-trip, and ensure the
    appended question conforms to QuizQuestion (not QuestionOut).
    """
    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    synopsis = state.get("synopsis")
    characters: List[CharacterProfile] = state.get("generated_characters") or []
    history = state.get("quiz_history") or []
    analysis = state.get("topic_analysis") or {}

    # v0: history is typed already
    history_payload = [h.model_dump() if hasattr(h, "model_dump") else h for h in (history or [])]
    existing = state.get("generated_questions") or []

    characters_payload = [c.model_dump() if hasattr(c, "model_dump") else c for c in (characters or [])]
    synopsis_payload = synopsis.model_dump() if hasattr(synopsis, "model_dump") else {"title": "", "summary": ""}

    q_raw = await tool_generate_next_question.ainvoke({
        "quiz_history": history_payload,
        "character_profiles": characters_payload,
        "synopsis": synopsis_payload,
        "analysis": analysis,
        "trace_id": trace_id,
        "session_id": str(session_id),
    })

    # Convert next QuestionOut → QuizQuestion → plain dict for state
    def _one_to_qq(obj) -> QuizQuestion:
        if isinstance(obj, QuizQuestion):
            return obj
        text = getattr(obj, "question_text", None) or (obj.get("question_text") if isinstance(obj, dict) else "")
        opts = getattr(obj, "options", None) or (obj.get("options") if isinstance(obj, dict) else [])
        norm_opts: List[Dict[str, str]] = []
        for o in opts or []:
            if hasattr(o, "model_dump"):
                o = o.model_dump()
            if isinstance(o, dict) and o.get("text"):
                item = {"text": str(o["text"])}
                if o.get("image_url"):
                    item["image_url"] = str(o["image_url"])
                norm_opts.append(item)
        return QuizQuestion.model_validate({"question_text": str(text), "options": norm_opts})

    qq = _one_to_qq(q_raw)
    qd = qq.model_dump(mode="json", exclude_none=True)
    return {"generated_questions": [*existing, qd]}

# ---------------------------------------------------------------------------
# Node: assemble_and_finish (sink)
# ---------------------------------------------------------------------------


async def _assemble_and_finish(state: GraphState) -> dict:
    """
    Sink node: logs a compact summary. Safe whether or not questions exist.
    """
    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    chars = state.get("generated_characters") or []
    qs = state.get("generated_questions") or []
    syn = state.get("synopsis")

    logger.info(
        "assemble.finish",
        session_id=session_id,
        trace_id=trace_id,
        has_synopsis=bool(syn),
        characters=len(chars),
        questions=len(qs),
    )

    summary = (
        f"Assembly summary → synopsis: {bool(syn)} | "
        f"characters: {len(chars)} | questions: {len(qs)}"
    )
    return {"messages": [AIMessage(content=summary)]}


# ---------------------------------------------------------------------------
# Router / conditionals
# ---------------------------------------------------------------------------


def _phase_router(state: GraphState) -> Literal["baseline", "adaptive", "end"]:
    """After characters: baseline if none yet; adaptive only after all baseline answered."""
    if not state.get("ready_for_questions"):
        return "end"
    have_baseline = bool(state.get("baseline_ready"))  # <-- explicit flag, not the list
    if not have_baseline:
        return "baseline"
    answered = len(state.get("quiz_history") or [])
    baseline_count = int(state.get("baseline_count") or 0)
    return "adaptive" if answered >= baseline_count else "end"


# ---------------------------------------------------------------------------
# Graph definition
# ---------------------------------------------------------------------------


workflow = StateGraph(GraphState)
logger.debug("graph.init", workflow_id=id(workflow))

# Nodes
workflow.add_node("bootstrap", _bootstrap_node)
workflow.add_node("generate_characters", _generate_characters_node)
workflow.add_node("generate_baseline_questions", _generate_baseline_questions_node)
workflow.add_node("decide_or_finish", _decide_or_finish_node)
workflow.add_node("generate_adaptive_question", _generate_adaptive_question_node)
workflow.add_node("assemble_and_finish", _assemble_and_finish)

# Entry
workflow.set_entry_point("bootstrap")

# Linear prep: bootstrap → generate_characters
workflow.add_edge("bootstrap", "generate_characters")

# Router: characters → (baseline | adaptive | END)
workflow.add_conditional_edges(
    "generate_characters",
    _phase_router,
    {"baseline": "generate_baseline_questions", "adaptive": "decide_or_finish", "end": END},
)

# If questions were generated, fan into sink then end
workflow.add_edge("generate_baseline_questions", "assemble_and_finish")
workflow.add_conditional_edges(
    "decide_or_finish",
    lambda s: "finish" if s.get("should_finalize") else "ask",
    {"finish": "assemble_and_finish", "ask": "generate_adaptive_question"},
)
workflow.add_edge("generate_adaptive_question", "assemble_and_finish")
workflow.add_edge("assemble_and_finish", END)

logger.debug(
    "graph.wired",
    edges=[
        ("bootstrap", "generate_characters"),
        ("generate_characters", "generate_baseline_questions/decide_or_finish/END via router"),
        ("generate_baseline_questions", "assemble_and_finish"),
        ("decide_or_finish", "assemble_and_finish/generate_adaptive_question via router"),
        ("generate_adaptive_question", "assemble_and_finish"),
        ("assemble_and_finish", "END"),
    ],
)

# ---------------------------------------------------------------------------
# Checkpointer factory (async Redis or in-memory)
# ---------------------------------------------------------------------------


async def create_agent_graph():
    """
    Compile the graph with a checkpointer.

    Prefers Redis (AsyncRedisSaver). If unavailable or disabled via
    USE_MEMORY_SAVER=1, falls back to MemorySaver.
    """
    logger.info("graph.compile.start", env=_env_name())
    t0 = time.perf_counter()

    env = _env_name()
    use_memory_saver = os.getenv("USE_MEMORY_SAVER", "").lower() in {"1", "true", "yes"}

    checkpointer = None
    cm = None  # async context manager (to close later)

    # Optional TTL in minutes for Redis saver
    ttl_env = os.getenv("LANGGRAPH_REDIS_TTL_MIN", "").strip()
    ttl_minutes: Optional[int] = None
    if ttl_env.isdigit():
        try:
            ttl_minutes = max(1, int(ttl_env))
        except Exception:
            ttl_minutes = None

    # Create saver
    if env in {"local", "dev", "development"} and use_memory_saver:
        checkpointer = MemorySaver()
        logger.info("graph.checkpointer.memory", env=env)
    else:
        try:
            # Try seconds-int API first
            if ttl_minutes:
                try:
                    cm = AsyncRedisSaver.from_conn_string(settings.REDIS_URL, ttl=ttl_minutes * 60)
                except TypeError:
                    # Fallback to dict-style config if library expects a config mapping
                    cm = AsyncRedisSaver.from_conn_string(settings.REDIS_URL, ttl={
                        "default_ttl": ttl_minutes * 60,
                        "refresh_on_read": True,
                    })
            else:
                cm = AsyncRedisSaver.from_conn_string(settings.REDIS_URL)

            checkpointer = await cm.__aenter__()
            # Some versions expose explicit setup
            if hasattr(checkpointer, "asetup"):
                await checkpointer.asetup()
            logger.info(
                "graph.checkpointer.redis.ok",
                ttl_minutes=ttl_minutes,
            )
        except Exception as e:
            logger.warning(
                "graph.checkpointer.redis.fail_fallback_memory",
                error=str(e),
                hint="Ensure Redis Stack with RedisJSON & RediSearch is available, or set USE_MEMORY_SAVER=1.",
            )
            checkpointer = MemorySaver()
            cm = None

    agent_graph = workflow.compile(checkpointer=checkpointer)

    # Attach handles so the app can close things on shutdown
    try:
        setattr(agent_graph, "_async_checkpointer", checkpointer)
        setattr(agent_graph, "_redis_cm", cm)
    except Exception:
        logger.debug("graph.checkpointer.attach.skip")

    dt_ms = round((time.perf_counter() - t0) * 1000, 1)
    logger.info("graph.compile.done", duration_ms=dt_ms, entry_point="bootstrap")
    return agent_graph


async def aclose_agent_graph(agent_graph) -> None:
    """Close the async Redis checkpointer context when present."""
    cm = getattr(agent_graph, "_redis_cm", None)
    cp = getattr(agent_graph, "_async_checkpointer", None)

    if hasattr(cp, "aclose"):
        try:
            await cp.aclose()
            logger.info("graph.checkpointer.redis.aclose.ok")
        except Exception as e:
            logger.warning(
                "graph.checkpointer.redis.aclose.fail", error=str(e), exc_info=True
            )

    if cm is not None and hasattr(cm, "__aexit__"):
        try:
            await cm.__aexit__(None, None, None)
            logger.info("graph.checkpointer.redis.closed")
        except Exception as e:
            logger.warning(
                "graph.checkpointer.redis.close.fail", error=str(e), exc_info=True
            )
