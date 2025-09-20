# backend/app/agent/graph.py
"""
Main Agent Graph (Azure/YAML-config aware)

This module defines the LangGraph agent that orchestrates the quiz-generation
workflow. It now supports two entry modes:

- **Agent-first (legacy / production)**: Entry at "agent" (dynamic planner + tools).
- **Bootstrap-first (local)**: Entry at "bootstrap" to deterministically create the
  synopsis, characters, and baseline questions before handing off to the agent loop.

Selection is driven by: settings.feature_flags.flow_mode == "local" → bootstrap-first.

We deliberately avoid touching checkpointing/Redis wiring. The checkpointer is
constructed the same way as before. The only *conditional* change at compile time
is the chosen entry point.
"""

from __future__ import annotations

from typing import Literal, Optional, Tuple, List, Dict, Any
import os
import time
import uuid

import structlog
import redis.asyncio as redis  # keep import to avoid surprising diffs for deps
from langchain_core.messages import AIMessage, HumanMessage, BaseMessage
from langgraph.checkpoint.redis import RedisSaver  # kept import; not used directly here
from langgraph.checkpoint.redis.aio import AsyncRedisSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from pydantic import BaseModel, ValidationError

from app.agent.state import GraphState, Synopsis, CharacterProfile, QuizQuestion
from app.agent.tools import get_tools
from app.agent.tools.planning_tools import InitialPlan  # preserves legacy initial plan
from app.agent.tools.analysis_tools import analyze_tool_error
from app.core.config import settings
from app.services.llm_service import llm_service  # we reuse the resilient, structured client

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_len(obj):
    try:
        return len(obj)  # type: ignore[arg-type]
    except Exception:
        return None


def _keys(obj):
    try:
        return list(obj.keys())  # type: ignore[assignment]
    except Exception:
        return None


def _env_name() -> str:
    try:
        return (settings.app.environment or "local").lower()
    except Exception:
        return "local"


def _should_use_local_bootstrap() -> bool:
    """
    Feature-flagged local flow. When 'local', we start at 'bootstrap'
    to deterministically create synopsis/characters/baseline questions.
    """
    try:
        return (settings.feature_flags.flow_mode or "agent").lower() == "local"
    except Exception:
        return False


# ---------------------------------------------------------------------------
# New bootstrap nodes (deterministic, config-driven)
# ---------------------------------------------------------------------------

async def _bootstrap_node(state: GraphState) -> dict:
    """
    Deterministically generate the quiz synopsis and the target character archetypes.
    - Uses legacy 'initial_planner' for robust archetype extraction.
    - Optionally refines synopsis with 'synopsis_generator' (if configured).
    - Enforces max character count (truncation); best-effort on min.
    - Leaves detailed character profile generation to the next node.
    """
    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    category = state.get("category") or (state["messages"][0].content if state.get("messages") else "")
    logger.info(
        "bootstrap_node.start",
        session_id=str(session_id),
        trace_id=trace_id,
        category=category,
        env=_env_name(),
    )

    # 1) Get initial plan for synopsis + ideal archetypes (backward-compatible)
    t0 = time.perf_counter()
    plan = await llm_service.get_structured_response(
        tool_name="initial_planner",
        messages=[HumanMessage(content=category)],
        response_model=InitialPlan,
        session_id=str(session_id),
        trace_id=trace_id,
    )
    dt_ms = round((time.perf_counter() - t0) * 1000, 1)
    logger.info(
        "bootstrap_node.initial_plan.ok",
        session_id=str(session_id),
        trace_id=trace_id,
        duration_ms=dt_ms,
        synopsis_chars=_safe_len(plan.synopsis),
        archetype_count=_safe_len(plan.ideal_archetypes),
    )

    # 2) Build synopsis object. If a dedicated synopsis generator is configured, prefer it.
    synopsis_obj = Synopsis(title=f"Quiz: {category}", summary=plan.synopsis)
    try:
        # If 'synopsis_generator' is configured, refine title/summary.
        # We keep this best-effort; failure falls back to initial plan synopsis.
        t1 = time.perf_counter()
        refined = await llm_service.get_structured_response(
            tool_name="synopsis_generator",
            messages=[HumanMessage(content=category)],
            response_model=Synopsis,
            session_id=str(session_id),
            trace_id=trace_id,
        )
        dt1_ms = round((time.perf_counter() - t1) * 1000, 1)
        if refined and refined.summary:
            synopsis_obj = refined
            logger.info(
                "bootstrap_node.synopsis.refined",
                session_id=str(session_id),
                trace_id=trace_id,
                duration_ms=dt1_ms,
            )
    except Exception as e:
        logger.debug(
            "bootstrap_node.synopsis.refine.skipped",
            session_id=str(session_id),
            trace_id=trace_id,
            reason=str(e),
        )

    # 3) Enforce character count bounds on archetypes (best-effort)
    min_chars = settings.quiz.min_characters
    max_chars = settings.quiz.max_characters
    archetypes: List[str] = plan.ideal_archetypes or []

    if len(archetypes) > max_chars:
        archetypes = archetypes[:max_chars]
        logger.debug(
            "bootstrap_node.archetypes.truncated",
            session_id=str(session_id),
            trace_id=trace_id,
            max_chars=max_chars,
        )

    if len(archetypes) < min_chars:
        # Best-effort: attempt to expand using character_list_generator (optional).
        # If it fails, we proceed with what we have to avoid blocking UX.
        try:
            t2 = time.perf_counter()
            # We pass a combined hint; llm_service/PromptManager will use the prompt.
            msg = f"Category: {category}\nSynopsis: {synopsis_obj.summary}"
            extra = await llm_service.get_structured_response(
                tool_name="character_list_generator",
                messages=[HumanMessage(content=msg)],
                response_model=type(
                    "ArchetypesOut",
                    (BaseModel,),
                    {"archetypes": (List[str], ...)},
                ),
                session_id=str(session_id),
                trace_id=trace_id,
            )
            dt2 = round((time.perf_counter() - t2) * 1000, 1)
            logger.info(
                "bootstrap_node.archetypes.expand.attempt",
                session_id=str(session_id),
                trace_id=trace_id,
                duration_ms=dt2,
                returned=_safe_len(getattr(extra, "archetypes", [])),
            )
            for name in getattr(extra, "archetypes", []):
                if len(archetypes) >= min_chars:
                    break
                if name not in archetypes:
                    archetypes.append(name)
        except Exception as e:
            logger.debug(
                "bootstrap_node.archetypes.expand.skipped",
                session_id=str(session_id),
                trace_id=trace_id,
                reason=str(e),
            )

    # Final clamp (never exceed max)
    if len(archetypes) > max_chars:
        archetypes = archetypes[:max_chars]

    logger.info(
        "bootstrap_node.done",
        session_id=str(session_id),
        trace_id=trace_id,
        synopsis_len=_safe_len(synopsis_obj.summary),
        archetype_count=len(archetypes),
        min_chars=min_chars,
        max_chars=max_chars,
    )

    # Prepare a concise AI message for observability / UI logs
    plan_summary = (
        f"Plan for '{category}'. Synopsis ready. "
        f"Target characters to create: {archetypes}"
    )

    return {
        "messages": [AIMessage(content=plan_summary)],
        "category": category,
        "category_synopsis": synopsis_obj,
        "ideal_archetypes": archetypes,
        # ensure error flags are present for downstream nodes
        "is_error": False,
        "error_message": None,
    }


async def _generate_characters_node(state: GraphState) -> dict:
    """
    Create detailed character profiles for each target archetype using the
    configured 'profile_writer' tool. This is deterministic and bounded:
    - We do not fan-out endlessly; we iterate serially (safe for API quotas).
    - We create between [min,max] characters (already clamped by bootstrap).
    """
    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    category = state.get("category")
    synopsis: Optional[Synopsis] = state.get("category_synopsis")
    archetypes: List[str] = state.get("ideal_archetypes") or []

    if not archetypes:
        return {
            "messages": [AIMessage(content="No archetypes available to generate characters.")],
            "generated_characters": [],
        }

    logger.info(
        "characters_node.start",
        session_id=str(session_id),
        trace_id=trace_id,
        target_count=len(archetypes),
        category=category,
    )

    # Container for CharacterProfile
    characters: List[CharacterProfile] = []

    for name in archetypes:
        try:
            # Prompt expects category and character_name in the template.
            # Passing a composed human message is compatible with existing llm_service.
            # Example content hints; PromptManager fills the template.
            hint = f"Category: {category}\nCharacter: {name}"
            t0 = time.perf_counter()
            prof = await llm_service.get_structured_response(
                tool_name="profile_writer",
                messages=[HumanMessage(content=hint)],
                response_model=CharacterProfile,
                session_id=str(session_id),
                trace_id=trace_id,
            )
            dt_ms = round((time.perf_counter() - t0) * 1000, 1)
            logger.debug(
                "characters_node.profile.ok",
                session_id=str(session_id),
                trace_id=trace_id,
                character=name,
                duration_ms=dt_ms,
            )
            characters.append(prof)
        except Exception as e:
            # Skip but continue; we want to create as many as possible within bounds.
            logger.warning(
                "characters_node.profile.fail",
                session_id=str(session_id),
                trace_id=trace_id,
                character=name,
                error=str(e),
            )

    logger.info(
        "characters_node.done",
        session_id=str(session_id),
        trace_id=trace_id,
        generated_count=len(characters),
    )

    return {
        "messages": [AIMessage(content=f"Generated {len(characters)} character profiles.")],
        "generated_characters": characters,
        "is_error": False,
        "error_message": None,
    }


# Simple structured model for baseline question parsing
class _QOut(BaseModel):
    id: Optional[str] = None
    question_text: str
    # Accept either strings or objects with 'text' for options; we normalize later.
    options: List[Any]


class _QList(BaseModel):
    questions: List[_QOut]


def _normalize_options(raw: List[Any]) -> List[Dict[str, str]]:
    """
    Accepts a list from the LLM; returns List[{'text': '...'}].
    """
    out: List[Dict[str, str]] = []
    for opt in raw:
        if isinstance(opt, str):
            t = opt.strip()
            if t:
                out.append({"text": t})
        elif isinstance(opt, dict):
            # prefer 'text' if present, else stringify
            txt = str(opt.get("text") or opt.get("label") or "").strip()
            if txt:
                out.append({"text": txt})
        else:
            # fallback stringify
            s = str(opt).strip()
            if s:
                out.append({"text": s})
    return out


async def _generate_baseline_questions_node(state: GraphState) -> dict:
    """
    Generate the first-n baseline questions with up to m options per question.
    - Uses 'question_generator' tool configuration.
    - Enforces n and m from settings.quiz (truncate if necessary).
    """
    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    category = state.get("category") or ""
    characters: List[CharacterProfile] = state.get("generated_characters") or []

    n = settings.quiz.baseline_questions_n
    m = settings.quiz.max_options_m

    logger.info(
        "baseline_node.start",
        session_id=str(session_id),
        trace_id=trace_id,
        requested_n=n,
        options_cap_m=m,
        character_count=len(characters),
    )

    # Provide a compact hint for the question generator; PromptManager uses its own template.
    # We try to include character names + short blurbs to guide the generator.
    char_hint = "\n".join(f"- {c.name}: {c.short_description}" for c in characters[: settings.quiz.max_characters])
    hint = f"Category: {category}\nCharacters:\n{char_hint}\nPlease create {n} baseline questions."

    t0 = time.perf_counter()
    raw = await llm_service.get_structured_response(
        tool_name="question_generator",
        messages=[HumanMessage(content=hint)],
        response_model=_QList,
        session_id=str(session_id),
        trace_id=trace_id,
    )
    dt_ms = round((time.perf_counter() - t0) * 1000, 1)

    # Normalize and enforce caps
    questions: List[QuizQuestion] = []
    for idx, q in enumerate(raw.questions[: n]):
        opts = _normalize_options(q.options)[:m]
        if not opts:
            # guarantee at least two options; if missing, synthesize dummies
            opts = [{"text": "Yes"}, {"text": "No"}]
        qid = q.id or f"q{idx+1}"
        questions.append(
            QuizQuestion(question_text=q.question_text, options=opts)
        )

    logger.info(
        "baseline_node.done",
        session_id=str(session_id),
        trace_id=trace_id,
        duration_ms=dt_ms,
        produced=len(questions),
    )

    return {
        "messages": [AIMessage(content=f"Baseline questions ready: {len(questions)}")],
        "generated_questions": questions,
        "is_error": False,
        "error_message": None,
    }


# ---------------------------------------------------------------------------
# Legacy agent planner + tool loop (unchanged logic)
# ---------------------------------------------------------------------------

tools = get_tools()
logger.info("Agent tools loaded", tool_count=_safe_len(tools))
_tool_runner = ToolNode(tools)
logger.debug("ToolNode initialized", tool_node_id=id(_tool_runner))


async def agent_node(state: GraphState) -> dict:
    """
    Legacy/dynamic planner node.
    First call (legacy path) uses InitialPlan if no synopsis; subsequent calls ask the planner LLM
    to decide which tool to call and returns an AIMessage possibly containing tool_calls.
    """
    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    messages = state["messages"]

    logger.debug(
        "agent_node",
        session_id=str(session_id),
        trace_id=trace_id,
        message_count=_safe_len(messages),
    )

    # On the very first invocation in legacy mode, create initial plan (if not already done by bootstrap)
    if len(messages) == 1 and not state.get("category_synopsis"):
        category = state["messages"][0].content
        logger.info("agent_node.initial_plan", session_id=str(session_id), trace_id=trace_id, category=category)
        t0 = time.perf_counter()
        initial_plan = await llm_service.get_structured_response(
            tool_name="initial_planner",
            messages=[HumanMessage(content=category)],
            response_model=InitialPlan,
            session_id=str(session_id),
            trace_id=trace_id,
        )
        dt_ms = round((time.perf_counter() - t0) * 1000, 1)
        synopsis_obj = Synopsis(title=f"Quiz: {category}", summary=initial_plan.synopsis)
        logger.info(
            "agent_node.initial_plan.ok",
            session_id=str(session_id),
            trace_id=trace_id,
            duration_ms=dt_ms,
            archetype_count=_safe_len(initial_plan.ideal_archetypes),
        )
        plan_summary = (
            f"Plan created for '{category}'. Synopsis ready. "
            f"Will now create characters: {initial_plan.ideal_archetypes}"
        )
        return {
            "messages": [AIMessage(content=plan_summary)],
            "category": category,
            "category_synopsis": synopsis_obj,
            "ideal_archetypes": initial_plan.ideal_archetypes,
        }

    # Subsequent turns: planner decides next tool (unchanged)
    logger.debug("agent_node.plan_next", session_id=str(session_id), trace_id=trace_id)
    t1 = time.perf_counter()
    response = await llm_service.get_agent_response(
        tool_name="planner",
        messages=messages,
        tools=[t.to_dict() for t in tools],
        session_id=str(session_id),
        trace_id=trace_id,
    )
    dt2_ms = round((time.perf_counter() - t1) * 1000, 1)
    logger.info(
        "agent_node.planner.ok",
        session_id=str(session_id),
        trace_id=trace_id,
        duration_ms=dt2_ms,
        has_tool_calls=bool(getattr(response, "tool_calls", None)),
        tool_call_count=_safe_len(getattr(response, "tool_calls", [])),
    )
    return {"messages": [response]}


async def tool_node(state: GraphState) -> dict:
    """
    Executes tools via ToolNode and normalizes error flags in state.
    """
    error_count = state.get("error_count", 0)
    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    logger.debug(
        "tool_node",
        session_id=str(session_id),
        trace_id=trace_id,
        error_count=error_count,
        last_message_type=type(state["messages"][-1]).__name__ if state.get("messages") else None,
    )
    try:
        t0 = time.perf_counter()
        new_state = await _tool_runner.ainvoke(state)
        dt_ms = round((time.perf_counter() - t0) * 1000, 1)
        logger.info(
            "tool_node.ok",
            session_id=str(session_id),
            trace_id=trace_id,
            duration_ms=dt_ms,
            new_state_keys=_keys(new_state),
        )
        # ensure flags
        new_state.setdefault("is_error", False)
        new_state.setdefault("error_message", None)
        new_state["error_count"] = error_count
        return new_state
    except Exception as e:
        logger.error(
            "tool_node.fail",
            session_id=str(session_id),
            trace_id=trace_id,
            error=str(e),
            exc_info=True,
        )
        return {"messages": [], "is_error": True, "error_message": f"Tool execution failed: {e}", "error_count": error_count + 1}


async def error_node(state: GraphState) -> dict:
    """
    Analyze last error and prepare a retry message. We keep retry counting compatible
    with prior logic (bounded by settings.agent.max_retries).
    """
    session_id = state.get("session_id")
    trace_id = state.get("trace_id")
    logger.warning(
        "error_node",
        session_id=str(session_id),
        trace_id=trace_id,
        error_count=state.get("error_count"),
        error_message=state.get("error_message"),
    )
    t0 = time.perf_counter()
    corrective_action = await analyze_tool_error.ainvoke({"error_message": state.get("error_message"), "state": dict(state)})
    dt_ms = round((time.perf_counter() - t0) * 1000, 1)
    logger.info(
        "error_node.analysis",
        session_id=str(session_id),
        trace_id=trace_id,
        duration_ms=dt_ms,
        corrective=str(corrective_action)[:200] if corrective_action is not None else None,
    )
    return {"messages": [AIMessage(content=f"Retrying after error: {corrective_action}")], "is_error": False}


# ---------------------------------------------------------------------------
# Conditional edges
# ---------------------------------------------------------------------------

def should_continue(state: GraphState) -> Literal["tools", "end"]:
    """
    If the last AIMessage includes tool_calls, continue to tools; else end.
    """
    last = state["messages"][-1]
    decision: Literal["tools", "end"] = "tools" if isinstance(last, AIMessage) and getattr(last, "tool_calls", None) else "end"
    logger.debug("edge.should_continue", decision=decision)
    return decision


def after_tools(state: GraphState) -> Literal["agent", "error", "end"]:
    """
    Handle tool errors with retry up to settings.agent.max_retries.
    """
    if state.get("is_error"):
        decision: Literal["agent", "error", "end"] = "end" if state.get("error_count", 0) >= settings.agent.max_retries else "error"
    else:
        decision = "agent"
    logger.debug("edge.after_tools", decision=decision, error=state.get("is_error"), error_count=state.get("error_count"))
    return decision


# ---------------------------------------------------------------------------
# Graph definition
# ---------------------------------------------------------------------------

workflow = StateGraph(GraphState)
logger.debug("graph.init", workflow_id=id(workflow))

# New deterministic bootstrap nodes
workflow.add_node("bootstrap", _bootstrap_node)
workflow.add_node("generate_characters", _generate_characters_node)
workflow.add_node("generate_baseline_questions", _generate_baseline_questions_node)
logger.debug("graph.nodes.added.bootstrap", nodes=["bootstrap", "generate_characters", "generate_baseline_questions"])

# Legacy planner + tools + error
workflow.add_node("agent", agent_node)
workflow.add_node("tools", tool_node)
workflow.add_node("error", error_node)
logger.debug("graph.nodes.added.legacy", nodes=["agent", "tools", "error"])

# Default entry remains 'agent'; we will override at compile time if local mode.
workflow.set_entry_point("agent")
logger.debug("graph.entry.set", entry_point="agent")

# Bootstrap chain (local mode): bootstrap → generate_characters → generate_baseline_questions → agent
workflow.add_edge("bootstrap", "generate_characters")
workflow.add_edge("generate_characters", "generate_baseline_questions")
workflow.add_edge("generate_baseline_questions", "agent")
logger.debug("graph.edges.bootstrap", edges=[("bootstrap","generate_characters"),("generate_characters","generate_baseline_questions"),("generate_baseline_questions","agent")])

# Planner path conditionals (unchanged)
workflow.add_conditional_edges("agent", should_continue, {"tools": "tools", "end": END})
workflow.add_conditional_edges("tools", after_tools, {"agent": "agent", "error": "error", "end": END})
workflow.add_edge("error", "agent")


# ---------------------------------------------------------------------------
# Checkpointer factory (unchanged except env read from settings.app.environment)
# ---------------------------------------------------------------------------

async def create_agent_graph():
    """
    Factory to compile the graph with a checkpointer. We keep Redis/Memory saver
    logic identical to prior implementation, and only choose the entry point
    based on feature flags to avoid breaking production.
    """
    logger.info("graph.compile.start", env=_env_name())
    t0 = time.perf_counter()

    env = _env_name()
    use_memory_saver = os.getenv("USE_MEMORY_SAVER", "").lower() in {"1", "true", "yes"}

    # Choose entry point based on feature flag (do NOT change production by default)
    if _should_use_local_bootstrap():
        workflow.set_entry_point("bootstrap")
        logger.info("graph.entry.override", entry_point="bootstrap")
    else:
        workflow.set_entry_point("agent")
        logger.info("graph.entry.override", entry_point="agent")

    checkpointer = None

    # Optional TTL in minutes for RedisSaver (kept for compatibility)
    ttl_env = os.getenv("LANGGRAPH_REDIS_TTL_MIN", "").strip()
    ttl_minutes: Optional[int] = None
    if ttl_env.isdigit():
        try:
            ttl_minutes = max(1, int(ttl_env))
        except Exception:
            ttl_minutes = None

    ttl_cfg = {"default_ttl": ttl_minutes, "refresh_on_read": True} if ttl_minutes else None

    if env in {"local", "dev", "development"} and use_memory_saver:
        checkpointer = MemorySaver()
        logger.info("graph.checkpointer.memory", env=env)
    else:
        try:
            # We deliberately keep using settings.REDIS_URL as before
            if ttl_cfg:
                checkpointer = AsyncRedisSaver.from_conn_string(settings.REDIS_URL, ttl=ttl_cfg)
            else:
                checkpointer = AsyncRedisSaver.from_conn_string(settings.REDIS_URL)
            await checkpointer.asetup()
            logger.info("graph.checkpointer.redis.ok", redis_url=settings.REDIS_URL, ttl_minutes=ttl_minutes)
        except Exception as e:
            logger.warning(
                "graph.checkpointer.redis.fail_fallback_memory",
                error=str(e),
                hint="Ensure Redis Stack/Modules available or enable USE_MEMORY_SAVER=1 for local."
            )
            checkpointer = MemorySaver()

    agent_graph = workflow.compile(checkpointer=checkpointer)

    try:
        setattr(agent_graph, "_async_checkpointer", checkpointer)
    except Exception:
        logger.debug("graph.checkpointer.attach.skip")

    dt_ms = round((time.perf_counter() - t0) * 1000, 1)
    logger.info("graph.compile.done", duration_ms=dt_ms, entry_point=("bootstrap" if _should_use_local_bootstrap() else "agent"))
    return agent_graph


async def aclose_agent_graph(agent_graph) -> None:
    """
    Explicitly close async Redis checkpointer when present.
    """
    cp = getattr(agent_graph, "_async_checkpointer", None)
    if hasattr(cp, "aclose"):
        try:
            await cp.aclose()
            logger.info("graph.checkpointer.redis.closed")
        except Exception as e:
            logger.warning("graph.checkpointer.redis.close.fail", error=str(e), exc_info=True)
