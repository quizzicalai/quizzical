# backend/tests/fixtures/agent_graph_fixtures.py
"""
Agent Graph test fixtures.

These fixtures and helpers target the LangGraph defined in `app.agent.graph`
and are designed for fast, deterministic tests that mirror production shapes.

What you get:
- agent_graph_memory_saver: compiles the real graph with an in-memory checkpointer
  (by temporarily setting settings.app.environment='local' and USE_MEMORY_SAVER=1).
- agent_thread_id: a fresh UUID per test for use as the thread_id.
- build_graph_config: convenience factory to attach `thread_id` (and optional db_session)
  to the runnable config (accessible from tools via config.get("configurable", {})).
- run_quiz_start: run the "start" phase (bootstrap → generate_characters → router(END)).
- run_quiz_proceed: flip the gate and generate baseline questions on the next run.
- get_graph_state: retrieve the latest saved state for assertions (tolerates different
  LangGraph return shapes); supports both .get_state and .aget_state.
- assert_synopsis_and_characters: tiny assertion helper used in tests.
- use_fake_agent_graph: patch app startup to use a deterministic in-memory FakeAgentGraph.

Key alignment rules:
- **State keys** are limited to those in AgentGraphStateModel:
  session_id, trace_id, category, messages, is_error, error_message, error_count,
  rag_context, outcome_kind, creativity_mode, topic_analysis, synopsis,
  ideal_archetypes, generated_characters, generated_questions, agent_plan,
  quiz_history, baseline_count, baseline_ready, ready_for_questions, should_finalize,
  current_confidence, final_result, last_served_index.
- **Nested shapes** match the canonical models in app.agent.schemas:
  - synopsis: { "title": str, "summary": str }
  - generated_characters: [{ "name", "short_description", "profile_text", "image_url"?: str }]
  - generated_questions: [{ "question_text", "options": [{ "text", "image_url"?: str }] }]
- No "view-model" or UI wrappers (e.g., no {"type": "synopsis", ...}) are ever stored
  in graph state; those are added at the API layer.
"""

from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, List, TypedDict

import pytest
import pytest_asyncio
from langchain_core.messages import HumanMessage

# Ensure `backend/` is importable (so `from app....` works when tests are run from repo root)
_THIS_FILE = Path(__file__).resolve()
_BACKEND_DIR = _THIS_FILE.parents[2]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# Real app graph & state
from app.agent.graph import (  # type: ignore
    create_agent_graph,
    aclose_agent_graph,
)
from app.agent.state import GraphState  # type: ignore
from app.core.config import settings  # type: ignore


# --------------------------------------------------------------------------------------
# Public config builder
# --------------------------------------------------------------------------------------

@dataclass
class GraphConfig:
    """
    Tiny wrapper for the "configurable" slot LangGraph/LangChain expose,
    with common knobs used by our tools.
    """
    thread_id: str
    db_session: Optional[Any] = None
    # Add more items as needed (e.g., feature flags) without breaking callers.

    def to_runnable_config(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"thread_id": self.thread_id}
        if self.db_session is not None:
            payload["db_session"] = self.db_session
        return {"configurable": payload}


def build_graph_config(
    session_uuid: uuid.UUID,
    *,
    db_session: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Create a LangGraph runnable config that carries a stable thread_id (required for checkpointing)
    and optional kb/DB handles that tools can read via `config.get("configurable", {})`.
    """
    cfg = GraphConfig(thread_id=str(session_uuid), db_session=db_session)
    return cfg.to_runnable_config()


# --------------------------------------------------------------------------------------
# Core graph fixtures (real graph with MemorySaver)
# --------------------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="function")
async def agent_graph_memory_saver(monkeypatch):
    """
    Compile the agent graph with an in-memory checkpointer (MemorySaver).

    Implementation details:
    - The graph factory prefers Redis unless `env in {"local","dev","development"}`
      AND `USE_MEMORY_SAVER` is truthy. We transiently set both so tests don't
      require Redis.
    - We ensure cleanup by calling the graph's provided async close helper.
    """
    # Force the code path that selects MemorySaver in create_agent_graph()
    monkeypatch.setenv("USE_MEMORY_SAVER", "1")
    try:
        # Some settings objects can be immutable; setattr(..., raising=False) keeps this tolerant.
        monkeypatch.setattr(settings.app, "environment", "local", raising=False)  # type: ignore[attr-defined]
    except Exception:
        # If settings.app doesn't exist, fall back to an env flag the function might consult.
        monkeypatch.setenv("APP_ENV", "local")

    graph = await create_agent_graph()
    try:
        yield graph
    finally:
        try:
            await aclose_agent_graph(graph)
        except Exception:
            pass


@pytest.fixture(scope="function")
def agent_thread_id() -> uuid.UUID:
    """Provide a fresh, stable UUID per test for use as the LangGraph thread_id."""
    return uuid.uuid4()


# --------------------------------------------------------------------------------------
# Helpers to run phases (functions, not fixtures, so you can compose freely)
# --------------------------------------------------------------------------------------

async def run_quiz_start(
    agent_graph,
    *,
    session_id: uuid.UUID,
    category: str = "Cats",
    trace_id: str = "test-trace",
    db_session: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Run the initial quiz phase.
    This executes: bootstrap → generate_characters → router(END) since the gate is off.

    Returns the latest state dict (values only) so tests can assert on synopsis, characters, etc.
    """
    initial_state: GraphState = {
        "session_id": session_id,
        "trace_id": trace_id,
        "category": category,
        # The graph tolerates missing messages, but including the first user message matches production shape.
        "messages": [HumanMessage(content=category)],
        "ready_for_questions": False,
    }
    config = build_graph_config(session_id, db_session=db_session)
    await agent_graph.ainvoke(initial_state, config=config)
    return await get_graph_state(agent_graph, session_id)


async def run_quiz_proceed(
    agent_graph,
    *,
    session_id: uuid.UUID,
    trace_id: str = "test-trace",
    db_session: Optional[Any] = None,
    expect_baseline: bool = True,
) -> Dict[str, Any]:
    """
    Flip the `ready_for_questions` gate and run the graph again.
    On this pass the router will direct to generate_baseline_questions (then assemble_and_finish).
    """
    delta: Dict[str, Any] = {
        "ready_for_questions": True,
        "trace_id": trace_id,
    }
    config = build_graph_config(session_id, db_session=db_session)
    await agent_graph.ainvoke(delta, config=config)
    state = await get_graph_state(agent_graph, session_id)

    # Optional sanity checks to catch regressions early (do not raise unless requested)
    if expect_baseline:
        qs = state.get("generated_questions") or []
        assert state.get("baseline_ready") is True, "Expected baseline_ready to be True after proceed()"
        assert isinstance(qs, list), "generated_questions should be a list"
        if qs:
            q0 = qs[0]
            assert isinstance(q0, dict), "Each generated_question should be a dict (QuizQuestion-like)"
            assert "question_text" in q0 and isinstance(q0["question_text"], str), "question_text missing or not a string"
            opts = q0.get("options") or []
            assert isinstance(opts, list), "options should be a list"
            assert all(
                isinstance(o, dict) and "text" in o and isinstance(o["text"], str)
                for o in opts
            ), "options should be list[{'text': str, ...}, ...]"
    return state


# --------------------------------------------------------------------------------------
# State access (works across LangGraph versions)
# --------------------------------------------------------------------------------------

async def get_graph_state(agent_graph, session_id: uuid.UUID) -> Dict[str, Any]:
    """
    Fetch the current values portion of the state for the given thread_id.

    LangGraph variants expose `get_state(config)` (sometimes sync) or `aget_state(config)` (async)
    that return either:
      - a `StateSnapshot`-like object with `.values` (preferred), or
      - a bare dict containing "values", or
      - already-returned values dicts.

    This helper tolerates all of the above.
    """
    cfg = build_graph_config(session_id)

    # Try async alias first if present
    try:
        aget = getattr(agent_graph, "aget_state", None)
        if callable(aget):
            snapshot = await aget(cfg)
        else:
            raise AttributeError
    except Exception:
        # Try async get_state
        try:
            snapshot = await agent_graph.get_state(cfg)  # type: ignore[attr-defined]
        except TypeError:
            # Older builds provide a sync method
            snapshot = agent_graph.get_state(cfg)  # type: ignore[attr-defined]
        except AttributeError:
            # Fallback: re-run a no-op and inspect return
            result = await agent_graph.ainvoke({}, config=cfg)
            if isinstance(result, dict):
                return result
            raise

    # Normalize to the `.values` dict
    try:
        if isinstance(snapshot, dict) and "values" in snapshot:
            return dict(snapshot["values"])
        values = getattr(snapshot, "values", None)
        if isinstance(values, dict):
            return dict(values)
    except Exception:
        pass

    if isinstance(snapshot, dict):
        return snapshot
    return {}


# --------------------------------------------------------------------------------------
# Example assertion helper (optional; handy in tests)
# --------------------------------------------------------------------------------------

def assert_synopsis_and_characters(state: Dict[str, Any], min_chars: int = 1) -> None:
    """
    Quick sanity assertion commonly used in tests that validate phase 1.

    NOTE:
    - State uses the internal key `synopsis` (Synopsis model shape),
      not the API/view key `category_synopsis`.
    """
    syn = state.get("synopsis")
    chars = state.get("generated_characters") or []
    assert syn is not None, "Expected a synopsis in state after run_quiz_start()"
    assert isinstance(chars, list) and len(chars) >= min_chars, f"Expected >= {min_chars} character profiles"


__all__ = [
    # fixtures
    "agent_graph_memory_saver",
    "agent_thread_id",
    # builders/helpers
    "build_graph_config",
    "run_quiz_start",
    "run_quiz_proceed",
    "get_graph_state",
    "assert_synopsis_and_characters",
    "use_fake_agent_graph",
]


# --------------------------------------------------------------------------------------
# Fake agent graph (used by API smoke tests)
# --------------------------------------------------------------------------------------

class FakeStateSnapshot:
    """Minimal snapshot wrapper with `.values` attribute to mirror LangGraph."""
    def __init__(self, values: dict) -> None:
        self.values = values


class _Option(TypedDict):
    text: str
    image_url: Optional[str]


class _Question(TypedDict):
    question_text: str
    options: List[_Option]


class FakeAgentGraph:
    """
    Minimal but production-shaped agent graph used by API tests.

    Design constraints:
    - **State keys** are exactly those allowed by AgentGraphStateModel
      (no extra top-level keys).
    - **Nested values** are plain dicts that Pydantic can coerce into:
        * Synopsis
        * CharacterProfile
        * QuizQuestion
    - Synopsis in state is ALWAYS:
        { "title": str, "summary": str }
      (no `"type": "synopsis"`; that wrapper is added in the API layer only).

    Behaviors:
      - `ainvoke`:
          * Merges deltas into stored state for the thread_id.
          * Phase 1: ensures `synopsis` and `generated_characters`.
          * Phase 2: when `ready_for_questions` is True and `baseline_ready` is False,
            sets `baseline_ready` and `generated_questions` with QuizQuestion-like dicts.
      - `get_state` / `aget_state`: return a snapshot-like object with `.values`.
      - `astream`: yields trivial ticks; not relied upon by HTTP tests, but present.
    """

    def __init__(self) -> None:
        # thread_id -> AgentGraphStateModel-shaped dict
        self._store: Dict[str, Dict[str, Any]] = {}

    # --- utilities -------------------------------------------------------------

    @staticmethod
    def _thread_id_from(config: dict) -> str:
        return str(config.get("configurable", {}).get("thread_id") or "thread")

    @staticmethod
    def _ensure_defaults(s: Dict[str, Any]) -> Dict[str, Any]:
        """
        Ensure defaults for all fields defined on AgentGraphStateModel.

        Only sets keys that are actually in AgentGraphStateModel; no extras.
        """
        # Error flags
        s.setdefault("is_error", False)
        s.setdefault("error_message", None)
        s.setdefault("error_count", 0)

        # Steering/context
        s.setdefault("rag_context", None)
        s.setdefault("outcome_kind", None)
        s.setdefault("creativity_mode", None)
        s.setdefault("topic_analysis", None)

        # Content
        s.setdefault("synopsis", None)
        s.setdefault("ideal_archetypes", [])
        s.setdefault("generated_characters", [])
        s.setdefault("generated_questions", [])

        # Progress / gating
        s.setdefault("agent_plan", None)
        s.setdefault("quiz_history", [])
        s.setdefault("baseline_count", 0)
        s.setdefault("baseline_ready", False)
        s.setdefault("ready_for_questions", False)
        s.setdefault("should_finalize", None)
        s.setdefault("current_confidence", None)

        # Final result
        s.setdefault("final_result", None)
        s.setdefault("last_served_index", None)

        return s

    @staticmethod
    def _mk_synopsis(category: str) -> Dict[str, Any]:
        """
        Internal synopsis state (matches Synopsis model exactly).

        IMPORTANT:
        - No "type" field here; the UI / API adds it when building responses.
        """
        title = f"Quiz: {category}".strip() if category else "Quiz: Untitled"
        return {
            "title": title,
            "summary": f"A friendly quiz exploring {category}." if category else "",
        }

    @staticmethod
    def _mk_characters(category: str) -> List[Dict[str, Any]]:
        """
        Character profiles shaped like CharacterProfile:

        {
          "name": str,
          "short_description": str,
          "profile_text": str,
          "image_url"?: str | None
        }
        """
        base = f"about {category}" if category else "about this topic"
        return [
            {
                "name": "The Optimist",
                "short_description": "Bright outlook",
                "profile_text": f"Always sees the good {base}.",
                "image_url": None,
            },
            {
                "name": "The Analyst",
                "short_description": "Thinks deeply",
                "profile_text": f"Loves data and logic {base}.",
                "image_url": None,
            },
            {
                "name": "The Traditionalist",
                "short_description": "Values the classics",
                "profile_text": f"Prefers the well-known aspects {base}.",
                "image_url": None,
            },
        ]

    @staticmethod
    def _mk_baseline_questions(category: str) -> List[_Question]:
        """
        Baseline questions shaped like QuizQuestion:

        {
          "question_text": str,
          "options": [{ "text": str, "image_url"?: str | None }, ...]
        }
        """
        label = category or "this topic"
        return [
            {
                "question_text": f"Which best describes an iconic element of {label}?",
                "options": [{"text": t, "image_url": None} for t in [
                    "Witty banter", "Fast cars", "Deep space", "Underwater cities"
                ]],
            },
            {
                "question_text": f"Pick the best-known setting related to {label}.",
                "options": [{"text": t, "image_url": None} for t in [
                    "Stars Hollow", "Gotham", "Metropolis", "The Shire"
                ]],
            },
            {
                "question_text": f"What vibe most fits {label}?",
                "options": [{"text": t, "image_url": None} for t in [
                    "Cozy", "Cyberpunk", "Noir", "Post-apocalyptic"
                ]],
            },
        ]

    # --- graph-like API --------------------------------------------------------

    async def ainvoke(self, state: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Merge the incoming delta into thread state and simulate the main phases:

        - Phase 1 (start):
            * ensure `synopsis` (Synopsis shape)
            * ensure `ideal_archetypes`
            * ensure `generated_characters` (CharacterProfile-like dicts)

        - Phase 2 (proceed with baseline):
            * when `ready_for_questions` is True and `baseline_ready` is False,
              generate QuizQuestion-like `generated_questions`, set `baseline_ready`
              and `baseline_count`.
        """
        thread_id = self._thread_id_from(config)
        current: Dict[str, Any] = dict(self._store.get(thread_id, {}))

        # Shallow merge of the incoming delta
        s: Dict[str, Any] = {**current, **(state or {})}
        s = self._ensure_defaults(s)

        # Category: mirror app behavior where category is normalized in bootstrap node.
        category = s.get("category") or "General"

        # --- Phase 1: synopsis + characters -----------------------------------
        if not s.get("synopsis"):
            s["synopsis"] = self._mk_synopsis(category)

        if not s.get("ideal_archetypes"):
            s["ideal_archetypes"] = ["The Optimist", "The Analyst", "The Traditionalist"]

        if not s.get("generated_characters"):
            s["generated_characters"] = self._mk_characters(category)

        # Basic agent_plan JSON (matches bootstrap's agent_plan_json shape)
        if s.get("synopsis") and s.get("agent_plan") is None:
            syn = s["synopsis"] or {}
            s["agent_plan"] = {
                "title": syn.get("title", "") or f"What {category} Are You?",
                "synopsis": syn.get("summary", "") or "",
                "ideal_archetypes": list(s.get("ideal_archetypes") or []),
            }

        # --- Phase 2: baseline questions --------------------------------------
        if s.get("ready_for_questions") and not s.get("baseline_ready"):
            qs = self._mk_baseline_questions(category)
            s["generated_questions"] = qs
            s["baseline_count"] = len(qs)
            s["baseline_ready"] = True
            # API typically serves from index 0 on next /next call
            if s.get("last_served_index") is None:
                s["last_served_index"] = -1

        # Persist state for this thread_id
        self._store[thread_id] = s
        return s

    async def astream(self, state: Dict[str, Any], config: Dict[str, Any]):
        """
        Simple stream stub: yields a couple "ticks".

        Not heavily used by HTTP API tests, but mirrors the existence of streaming.
        """
        thread_id = self._thread_id_from(config)
        yield {"tick": 1}

        # Ensure defaults & characters lazily if someone streams before calling /start
        s: Dict[str, Any] = dict(self._store.get(thread_id, {}))
        s = self._ensure_defaults({**s, **(state or {})})
        category = s.get("category") or "General"

        if not s.get("synopsis"):
            s["synopsis"] = self._mk_synopsis(category)
        if not s.get("ideal_archetypes"):
            s["ideal_archetypes"] = ["The Optimist", "The Analyst", "The Traditionalist"]
        if not s.get("generated_characters"):
            s["generated_characters"] = self._mk_characters(category)

        self._store[thread_id] = s
        yield {"tick": 2}

    async def get_state(self, config: Dict[str, Any]) -> FakeStateSnapshot:
        """
        Async `get_state` to mirror the compiled LangGraph interface.
        """
        thread_id = self._thread_id_from(config)
        return FakeStateSnapshot(values=self._store.get(thread_id, {}))

    async def aget_state(self, config: Dict[str, Any]) -> FakeStateSnapshot:
        """
        Async alias for get_state, for forward-compat.
        """
        return await self.get_state(config)

    async def aclose(self) -> None:  # pragma: no cover
        """Clear in-memory store."""
        self._store.clear()

    def __repr__(self) -> str:
        return f"<FakeAgentGraph threads={len(self._store)}>"


# --------------------------------------------------------------------------------------
# Fixture: patch app graph to use FakeAgentGraph
# --------------------------------------------------------------------------------------

@pytest.fixture(scope="function")
def use_fake_agent_graph(monkeypatch):
    """
    Patch app startup to use FakeAgentGraph for THIS test only.

    Use via: @pytest.mark.usefixtures("use_fake_agent_graph")

    We patch the *app.main* module factory/closer, because the FastAPI lifespan uses
    those symbols to initialize and tear down the agent graph for the HTTP layer.
    """
    import app.main as main_mod
    import app.agent.graph as graph_mod

    async def _create():
        return FakeAgentGraph()

    async def _close(_graph):
        try:
            await _graph.aclose()  # tolerant
        except Exception:
            pass

    # Patch both the indirection on main *and* the direct module used by lifespan
    monkeypatch.setattr(main_mod, "create_agent_graph", _create, raising=True)
    monkeypatch.setattr(main_mod, "aclose_agent_graph", _close, raising=True)
    monkeypatch.setattr(graph_mod, "create_agent_graph", _create, raising=True)
    monkeypatch.setattr(graph_mod, "aclose_agent_graph", _close, raising=True)
